"""Regressão (CC-306): os modelos com FK para outras tabelas registram os alvos sozinhos.

Num processo que importa só a rota de análises/tentativas, a FK para
`ml_model_versions`/`decision_points` precisa resolver sem depender de outro import.
"""

import subprocess
import sys


def test_analysis_and_learning_models_resolve_foreign_keys_in_isolation() -> None:
    code = (
        "import riftlab.learning.models\n"
        "from sqlalchemy.orm import configure_mappers\n"
        "from riftlab.shared.db import Base\n"
        "configure_mappers()\n"
        "for name in ('analyses', 'learning_attempts'):\n"
        "    [fk.column for fk in Base.metadata.tables[name].foreign_keys]\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
