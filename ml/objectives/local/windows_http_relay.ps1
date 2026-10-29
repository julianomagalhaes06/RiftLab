param(
    [Parameter(Mandatory=$true)][string]$CompletionUrl,
    [string]$KeyFile
)
# JSON-lines over inherited WSL/Windows pipes; the model stays on Windows loopback.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Net.Http
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.UseProxy = $false
$client = New-Object System.Net.Http.HttpClient($handler)
$client.Timeout = [TimeSpan]::FromSeconds(13)
if ($KeyFile) {
    $key = [IO.File]::ReadAllText($KeyFile).Trim()
    $client.DefaultRequestHeaders.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer', $key)
}
try {
    while (($line = [Console]::ReadLine()) -ne $null) {
        $content = $null
        $response = $null
        try {
            $content = New-Object System.Net.Http.StringContent($line, [Text.Encoding]::UTF8, 'application/json')
            $response = $client.PostAsync($CompletionUrl, $content).GetAwaiter().GetResult()
            $response.EnsureSuccessStatusCode() | Out-Null
            $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
            if ($body.content -isnot [string]) { throw 'Invalid upstream response' }
            [Console]::WriteLine((@{ content = $body.content } | ConvertTo-Json -Compress))
        } catch {
            [Console]::WriteLine('{"error":"model_unavailable"}')
        } finally {
            if ($response) { $response.Dispose() }
            if ($content) { $content.Dispose() }
        }
    }
} finally { $client.Dispose(); $handler.Dispose() }
