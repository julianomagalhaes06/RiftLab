param(
    [Parameter(Mandatory=$true)][string]$Server,
    [Parameter(Mandatory=$true)][string]$Model,
    [Parameter(Mandatory=$true)][string]$Adapter,
    [string]$Requests,
    [Parameter(Mandatory=$true)][string]$Output,
    [int]$Port = 18765,
    [int]$MaxRssMiB = 2048,
    [int]$MinFreeMiB = 2048,
    [string]$KeyFile,
    [int]$ServeSeconds = 0
)
# Native Windows/Vulkan probe. Never changes drivers, firewall, or application settings.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ProbeMemory {
    [StructLayout(LayoutKind.Sequential)] public struct Status {
        public uint length, load;
        public ulong totalPhysical, availablePhysical, totalPageFile, availablePageFile;
        public ulong totalVirtual, availableVirtual, availableExtendedVirtual;
    }
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GlobalMemoryStatusEx(ref Status status);
    public static ulong Available() {
        var s = new Status(); s.length = (uint)Marshal.SizeOf(s);
        if (!GlobalMemoryStatusEx(ref s)) throw new InvalidOperationException("Memory query failed");
        return s.availablePhysical;
    }
}
'@
$Output = [IO.Path]::GetFullPath($Output)
[IO.Directory]::CreateDirectory($Output) | Out-Null
$records = New-Object System.Collections.Generic.List[object]
$script:peakRss = 0L
$script:peakPrivate = 0L
$script:minAvailable = [long][ProbeMemory]::Available()
$process = $null
$failure = $null
$client = New-Object System.Net.Http.HttpClient
$client.Timeout = [TimeSpan]::FromSeconds(15)
$baseUrl = "http://127.0.0.1:$Port"
$authHeaders = @{}
$clock = [Diagnostics.Stopwatch]::StartNew()
function Check-Resources {
    $process.Refresh()
    if ($process.HasExited) { throw "Model server exited: $($process.ExitCode)" }
    $script:peakRss = [Math]::Max($script:peakRss, $process.PeakWorkingSet64)
    $script:peakPrivate = [Math]::Max($script:peakPrivate, $process.PrivateMemorySize64)
    $script:minAvailable = [Math]::Min($script:minAvailable, [long][ProbeMemory]::Available())
    if ($script:peakRss -gt $MaxRssMiB * 1MB) { throw 'Server RSS budget exceeded' }
    if ([ProbeMemory]::Available() -lt $MinFreeMiB * 1MB) { throw 'Windows free memory budget exceeded' }
    if ($clock.Elapsed.TotalSeconds -gt [Math]::Max(600, $ServeSeconds + 90)) { throw 'Total probe time exceeded' }
}
function Start-Completion($row) {
    $body = @{
        prompt = $row.prompt; n_predict = 12; temperature = 0; seed = 0
        repeat_penalty = 1.0; cache_prompt = $false; stream = $false
    } | ConvertTo-Json -Compress
    $content = New-Object System.Net.Http.StringContent($body, [Text.Encoding]::UTF8, 'application/json')
    return @{ task = $client.PostAsync("$baseUrl/completion", $content)
        content = $content; row = $row; started = [Diagnostics.Stopwatch]::StartNew() }
}
function Finish-Completion($pending, $phase) {
    while (!$pending.task.IsCompleted) { Check-Resources; Start-Sleep -Milliseconds 100 }
    $response = $pending.task.GetAwaiter().GetResult()
    try {
        $response.EnsureSuccessStatusCode() | Out-Null
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
        $records.Add([ordered]@{
            id = $pending.row.id; label = $pending.row.label; category = $pending.row.category
            phase = $phase; raw = $body.content; latencyS = $pending.started.Elapsed.TotalSeconds
            timings = $body.timings
        })
    } finally { $response.Dispose(); $pending.content.Dispose() }
}
try {
    if ($ServeSeconds -lt 0 -or $ServeSeconds -gt 3600) { throw 'ServeSeconds must be between 0 and 3600' }
    if (!$ServeSeconds) {
        $rows = Get-Content -Raw -Encoding UTF8 $Requests | ConvertFrom-Json
        if ($rows.Count -lt 2) { throw 'At least two development requests are required' }
        foreach ($row in $rows) {
            if ($row.prompt -isnot [string] -or $row.id -isnot [string]) {
                throw 'Expected individual requests with string prompts and IDs'
            }
        }
    }
    if ($KeyFile) {
        $key = [IO.File]::ReadAllText([IO.Path]::GetFullPath($KeyFile)).Trim()
        if (!$key -or $key -match '\s') { throw 'Invalid API key file' }
        $authHeaders = @{ Authorization = "Bearer $key" }
        $client.DefaultRequestHeaders.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer', $key)
    }
    if ([ProbeMemory]::Available() -lt ($MinFreeMiB + 128) * 1MB) {
        throw 'Insufficient free memory before starting server'
    }
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw 'Probe port is already in use'
    }
    $arguments = @('-m', ('"' + [IO.Path]::GetFullPath($Model) + '"'),
        '--lora', ('"' + [IO.Path]::GetFullPath($Adapter) + '"'),
        '--device', 'Vulkan0', '-ngl', '99', '-c', '1024', '-b', '128', '-ub', '64',
        '-np', '1', '-t', '4', '-tb', '4', '--cache-ram', '0',
        '--host', '127.0.0.1', '--port', "$Port", '--no-webui', '--load-mode', 'none',
        '--cors-origins', 'http://127.0.0.1')
    if ($KeyFile) { $arguments += @('--api-key-file', ('"' + [IO.Path]::GetFullPath($KeyFile) + '"')) }
    $process = Start-Process -FilePath $Server -ArgumentList $arguments -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $Output 'server.stdout.log') `
        -RedirectStandardError (Join-Path $Output 'server.stderr.log')
    $process.Id | Set-Content (Join-Path $Output 'server.pid')
    while ($true) {
        Check-Resources
        try {
            $health = Invoke-RestMethod "$baseUrl/health" -Headers $authHeaders -TimeoutSec 1
            if ($health.status -eq 'ok') { break }
        } catch { }
        if ($clock.Elapsed.TotalSeconds -gt 90) { throw 'Model load timeout' }
        Start-Sleep -Milliseconds 200
    }
    $loadS = $clock.Elapsed.TotalSeconds
    @{ ready = $true; loadS = $loadS } | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $Output 'ready.json')
    if ($ServeSeconds) {
        $until = $clock.Elapsed.TotalSeconds + $ServeSeconds
        while ($clock.Elapsed.TotalSeconds -lt $until) { Check-Resources; Start-Sleep -Milliseconds 200 }
    } else {
    foreach ($row in $rows) {
        Finish-Completion (Start-Completion $row) 'sequential'
        Check-Resources
    }
    # Start both before waiting, so queueing is included in end-to-end latency.
    $first = Start-Completion $rows[0]
    $second = Start-Completion $rows[1]
    Finish-Completion $first 'concurrent'
    Finish-Completion $second 'concurrent'
    Check-Resources
    }
} catch {
    $failure = $_.Exception.Message
} finally {
    if ($process -and !$process.HasExited) { Stop-Process -Id $process.Id -Force }
    $client.Dispose()
    $records.ToArray() | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 (Join-Path $Output 'responses.json')
    [ordered]@{
        status = $(if ($failure) { 'failed' } else { 'completed' }); failure = $failure
        loadS = $loadS; elapsedS = $clock.Elapsed.TotalSeconds
        peakRssMiB = $script:peakRss / 1MB; peakPrivateMiB = $script:peakPrivate / 1MB
        minWindowsAvailableMiB = $script:minAvailable / 1MB
        maxRssMiB = $MaxRssMiB; minFreeMiB = $MinFreeMiB
        requests = $records.Count
    } | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $Output 'resources.json')
}
if ($failure) { Write-Error $failure; exit 1 }
Get-Content (Join-Path $Output 'resources.json')
