"""Build an allowlisted CC-701 bundle without raw data, account data or original match IDs."""
import hashlib
import json
import re
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EVIDENCE = ROOT / '.private/validation/sprint-7/CC-701'
SOURCE_FILES = (
    '__init__.py', 'dataset/__init__.py', 'dataset/popularity.py',
    'ml/__init__.py', 'ml/features.py', 'ml/context.py', 'ml/late.py',
    'ml/late_pool.py', 'ml/metrics.py',
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    destination = HERE / 'riftlab-cc701-colab.zip'
    if destination.exists():
        raise RuntimeError('Pacote já existe; não sobrescrever')
    original = (EVIDENCE / 'late-dataset-v1.json').read_bytes()
    doc = json.loads(original)
    assert not doc['partial'] and len(doc['rows']) == 5004
    ids = sorted({r['matchId'] for r in doc['rows']})
    alias = {m: f'm{i:06d}' for i, m in enumerate(ids, 1)}
    dataset = {'schemaVersion': doc['schemaVersion'], 'matchIdsPseudonymized': True,
               'originalDatasetSha256': digest(original), 'counts': doc['counts'],
               'rows': [{**r, 'matchId': alias[r['matchId']]} for r in doc['rows']]}
    # Verify every value except the deliberately replaced grouping key is unchanged.
    assert all({k: v for k, v in a.items() if k != 'matchId'} ==
               {k: v for k, v in b.items() if k != 'matchId'}
               for a, b in zip(doc['rows'], dataset['rows'], strict=True))
    payload = (json.dumps(dataset, sort_keys=True, separators=(',', ':')) + '\n').encode()
    assert not re.search(rb'BR1_[0-9]+', payload)
    for key in ('subject_hash', 'puuid', 'riot_id', 'summoner_name', 'api_key'):
        assert key.encode() not in payload
    files = {'data/late-dataset-v1.json': payload,
             'train_colab.py': (HERE / 'train_colab.py').read_bytes(),
             'requirements-colab.txt': b'catboost==1.2.10\nnumpy==2.5.3\n'}
    for name, src in (
        ('champions', '.private/validation/sprint-4/CC-402/catalog/champion-16.19.1-en_US.json'),
        ('items', '.private/validation/sprint-3/CC-321/catalog/item-16.19.1-en_US.json'),
    ):
        files[f'catalog/{name}.json'] = (ROOT / src).read_bytes()
    for name in SOURCE_FILES:
        files[f'src/riftlab/{name}'] = (ROOT / 'backend/src/riftlab' / name).read_bytes()
    plan = {'card': 'CC-701', 'task': 'late_planning_v1', 'featureSchema': 'late_context_v1',
            'protocolSha256': digest((EVIDENCE / '07-protocolo-implementacao.md').read_bytes()),
            'amendmentSha256': digest((EVIDENCE / '17-colab-amendment.md').read_bytes()),
            'iterations': 200, 'depths': [4, 6], 'learningRate': .1, 'l2': 10, 'seed': 701,
            'baseline': ['champion_ordinal', 'owned_build_conditioned'],
            'primaryOrdinal': 4, 'support': {'trainRows': 60, 'distinctTrainMatches': 10},
            'testPreviouslyUsed': True, 'promotionAutomatic': False,
            'representation': 'FeaturesData_float32', 'threads': 2}
    files['PLAN.json'] = (json.dumps(plan, sort_keys=True, indent=1) + '\n').encode()
    manifest = {'card': 'CC-701', 'schemaVersion': 1,
                'originalDatasetSha256': digest(original),
                'files': {name: digest(raw) for name, raw in sorted(files.items())}}
    files['MANIFEST.json'] = (json.dumps(manifest, sort_keys=True, indent=1) + '\n').encode()
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            archive.writestr(name, raw)
    with (HERE / 'bundle-map-private.json').open('x') as stream:
        json.dump({'alias': alias}, stream, sort_keys=True)
    (HERE / 'bundle-map-private.json').chmod(0o600)
    h = digest(destination.read_bytes())
    (HERE / 'riftlab-cc701-colab.zip.sha256').write_text(f'{h}  {destination.name}\n')
    print(json.dumps({'zip': str(destination), 'sha256': h, 'files': len(files),
                      'matchesPseudonymized': len(alias), 'rows': len(doc['rows']),
                      'sizeMiB': round(destination.stat().st_size / 1024**2, 2)}))


if __name__ == '__main__':
    main()
