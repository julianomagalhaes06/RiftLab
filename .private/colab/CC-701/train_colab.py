"""CC-701: verified remote execution; no training outside Colab.

--check-only verifies hashes, splits and memory estimates without allocating a full
matrix or fitting anything. Selection is frozen before a single test attempt.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import random
import resource
import sys
import time
from collections import defaultdict
from pathlib import Path

from riftlab.ml.features import Catalogs
from riftlab.ml.late import fit_build, supported_cells
from riftlab.ml.late_pool import make_pool, matrix_size, scorer
from riftlab.ml.metrics import evaluate, rank_of


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, doc):
    with path.open('x') as stream:
        json.dump(doc, stream, indent=1, sort_keys=True)
        stream.write('\n')


def verify(bundle):
    manifest = json.loads((bundle / 'MANIFEST.json').read_text())
    for name, digest in manifest['files'].items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or sha(bundle / name) != digest:
            raise RuntimeError(f'Arquivo inválido: {name}')
    return manifest


def paired(rows, scores, a, b):
    groups = defaultdict(list)
    for row in rows:
        ids, target = row['candidateItemIds'], row['targetItemId']
        groups[row['matchId']].append(
            1 / rank_of(target, ids, scores[a](row)) - 1 / rank_of(target, ids, scores[b](row))
        )
    if not groups:
        return {'n': 0, 'deltaMrr': None, 'ci95': None}
    keys = sorted(groups)
    rng, samples = random.Random(701), []
    for _ in range(1000):
        vals = [v for _ in keys for v in groups[keys[rng.randrange(len(keys))]]]
        samples.append(sum(vals) / len(vals))
    vals = [v for group in groups.values() for v in group]
    samples.sort()
    return {'n': len(vals), 'matches': len(keys), 'deltaMrr': sum(vals) / len(vals),
            'ci95': [samples[24], samples[974]]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('out', type=Path)
    parser.add_argument('--device', choices=('CPU', 'GPU'), default='GPU')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    bundle, out = args.bundle.resolve(), args.out.resolve()
    manifest = verify(bundle)
    data = json.loads((bundle / 'data/late-dataset-v1.json').read_bytes())
    rows = data['rows']
    train = [r for r in rows if r['split'] == 'train']
    cells = supported_cells(train)
    parts = {s: [r for r in rows if r['split'] == s and
                 (r['championId'], r['mainItemOrdinal']) in cells]
             for s in ('train', 'validation', 'test')}
    catalogs = Catalogs.from_data_dragon(
        json.loads((bundle / 'catalog/champions.json').read_bytes()),
        json.loads((bundle / 'catalog/items.json').read_bytes()))
    sizes = {s: matrix_size(rs, catalogs, True) for s, rs in parts.items()}
    required = 3 * (sizes['train'] + sizes['validation']) + 2 * 1024**3
    check = {'rows': len(rows), 'supported': {s: len(rs) for s, rs in parts.items()},
             'matrixBytes': sizes, 'requiredAvailableBytes': required,
             'manifestSha256': sha(bundle / 'MANIFEST.json')}
    print('PREFLIGHT', json.dumps(check), flush=True)
    if args.check_only:
        return
    # No local training, including smoke runs, on the user's machine.
    if not Path('/content/drive/MyDrive').is_dir():
        raise RuntimeError('Treino permitido somente no Colab com Google Drive montado')
    import catboost
    import numpy
    from catboost import CatBoostRanker
    from catboost.utils import get_gpu_device_count
    if catboost.__version__ != '1.2.10' or numpy.__version__ != '2.5.3':
        raise RuntimeError('Versões divergentes: instale requirements-colab.txt num processo novo')
    out.mkdir(parents=True, exist_ok=True)
    identity = {'manifestSha256': check['manifestSha256'], 'taskType': args.device}
    if (out / 'identity.json').exists():
        if json.loads((out / 'identity.json').read_text()) != identity:
            raise RuntimeError('Não misturar pacote/dispositivo com uma execução anterior')
    else:
        write(out / 'identity.json', identity)
    if (out / 'result.json').exists():
        result = json.loads((out / 'result.json').read_text())
        for name, meta in result['models'].items():
            if sha(out / f'{name}.cbm') != meta['sha256']:
                raise RuntimeError('Modelo concluído diverge do relatório')
        print('Já concluído; nenhum treino ou teste repetido.', flush=True)
        return
    if (out / 'test-started.json').exists():
        raise RuntimeError('Teste iniciado sem conclusão. Preserve e devolva o ZIP parcial; não refazer.')
    if args.device == 'GPU' and get_gpu_device_count() < 1:
        raise RuntimeError('GPU indisponível. Selecione T4 no Colab ou use CPU explicitamente no Colab.')
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))) * 1024
    if available < required:
        raise MemoryError(f'RAM disponível {available}; necessário >= {required}. Nenhuma matriz alocada.')
    build = fit_build(train)
    def b0(row):
        return [build.base.score(row['championId'], row['mainItemOrdinal'], c['itemId'])
                for c in row['candidates']]
    scores = {'B0': b0, 'Bbuild': build.score}
    started = time.monotonic()
    report = {'card': 'CC-701', 'task': 'late_planning_v1', 'identity': identity,
              'inputs': manifest['files'], 'originalDatasetSha256': data['originalDatasetSha256'],
              'preflight': check, 'availableBytesAtStart': available,
              'supportCells': sorted(cells), 'coverage': {s: {'before': sum(r['split'] == s for r in rows),
                    'supported': len(rs)} for s, rs in parts.items()}, 'models': {},
              'environment': {'python': sys.version, 'machine': platform.machine(),
                    'catboost': catboost.__version__, 'numpy': numpy.__version__,
                    'threads': 2, 'taskType': args.device, 'matrix': 'FeaturesData_float32'},
              'limits': ['test previously used', 'historical imitation only',
                    'no hidden enemy inventories', 'no human late strategic validation',
                    'player overlap unknown; no player-disjoint claim']}
    selection = out / 'selection.json'
    if selection.exists():
        report = json.loads(selection.read_text())
    for name, composition in [('M1', True), ('M1-no-composition', False)]:
        meta_path, model_path = out / f'{name}.json', out / f'{name}.cbm'
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if sha(model_path) != meta['sha256']:
                raise RuntimeError('Checkpoint de modelo alterado')
            selected = CatBoostRanker()
            selected.load_model(str(model_path))
            print(f'{name}: checkpoint verificado, sem repetir treino', flush=True)
        else:
            if selection.exists() or model_path.exists():
                raise RuntimeError('Checkpoint parcial incoerente; devolver ZIP sem sobrescrever')
            print(f'{name}: alocando arrays compactos', flush=True)
            tp = make_pool(parts['train'], catalogs, composition=composition, max_bytes=sizes['train'])
            vp = make_pool(parts['validation'], catalogs, composition=composition,
                           max_bytes=sizes['validation'])
            best, grid = None, []
            for depth in (4, 6):
                model = CatBoostRanker(iterations=200, depth=depth, learning_rate=.1,
                    l2_leaf_reg=10, loss_function='PairLogit', random_seed=701, thread_count=2,
                    task_type=args.device, verbose=50, allow_writing_files=False)
                t = time.monotonic()
                model.fit(tp, eval_set=vp, use_best_model=False)
                score = scorer(model, catalogs, composition)
                metric = evaluate([r for r in parts['validation'] if r['mainItemOrdinal'] == 4],
                                  score, resamples=0)['all']['point']['mrr']
                grid.append({'depth': depth, 'validationMrrOrdinal4': metric,
                             'seconds': time.monotonic() - t})
                print(json.dumps({'model': name, **grid[-1]}), flush=True)
                if best is None or metric > best[0]:
                    best = (metric, model, depth)
            selected = best[1]
            selected.save_model(str(model_path))
            meta = {'depth': best[2], 'grid': grid, 'sha256': sha(model_path),
                    'featureNames': list(selected.feature_names_)}
            write(meta_path, meta)
            del tp, vp, best, model
            gc.collect()
        report['models'][name] = meta
        scores[name] = scorer(selected, catalogs, composition)
    if not selection.exists():
        report['validation'] = {n: evaluate(parts['validation'], s, resamples=1000, seed=701)
                                for n, s in scores.items()}
        baseline = max(('B0', 'Bbuild'), key=lambda n:
                       report['validation'][n]['ordinal:4']['point']['mrr'])
        report['selectedBaseline'] = baseline
        report['paired'] = {'validation': {n: paired(
            [r for r in parts['validation'] if r['mainItemOrdinal'] == 4], scores, 'M1', n)
            for n in (baseline, 'M1-no-composition')}}
        write(selection, report)
    baseline = report['selectedBaseline']
    write(out / 'test-started.json', {'selectionSha256': sha(selection), 'startedUtc': time.time()})
    print('Seleção congelada. Avaliação única do teste.', flush=True)
    report['test'] = {n: evaluate(parts['test'], s, resamples=1000, seed=701)
                      for n, s in scores.items()}
    report['paired']['test'] = {n: paired(
        [r for r in parts['test'] if r['mainItemOrdinal'] == 4], scores, 'M1', n)
        for n in (baseline, 'M1-no-composition')}
    delta = report['paired']['test'][baseline]
    vd = report['paired']['validation'][baseline]
    def hit(split, name):
        return report[split][name]['ordinal:4']['point']['hitAt3']
    report['gate'] = {'technicalPass': bool(vd['deltaMrr'] >= .02 and vd['ci95'][0] > 0
        and delta['deltaMrr'] >= .02 and delta['ci95'][0] > 0
        and all(hit(s, 'M1') >= hit(s, baseline) - .02 for s in ('validation', 'test'))
        and all(report['paired'][s]['M1-no-composition']['deltaMrr'] > 0
                for s in ('validation', 'test'))), 'promote': False,
        'strategicReview': 'NOT_EXECUTED_REQUIRES_HUMAN'}
    report['secondsThisSession'] = time.monotonic() - started
    report['maxRssMb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    write(out / 'result.json', report)
    print('CONCLUÍDO', json.dumps(report['gate']), flush=True)


if __name__ == '__main__':
    main()
