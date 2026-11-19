"""Separate reproducible fresh installs from compatible, offline upgrades."""
from importlib import import_module, metadata
from pathlib import Path
import sys


def check(root=None, imports=True, compatible=False):
    if compatible and sys.version_info < (3,11):
        raise RuntimeError('沿用旧环境需要 Python 3.11 或更高版本。')
    if not compatible and sys.version_info[:2] not in {(3,11),(3,12)}:
        raise RuntimeError('本包支持 Python 3.11 或 3.12；其他版本尚未验收。')
    from packaging.requirements import Requirement
    root = Path(root) if root else Path(__file__).resolve().parent
    errors = []
    requirements = (root/('requirements-compatible.txt' if compatible else 'requirements.txt')).read_text()
    for line in requirements.splitlines():
        line = line.strip().rstrip('\\').strip()
        if not line or line.startswith(('#','--')): continue
        req = Requirement(line)
        if req.marker and not req.marker.evaluate(): continue
        try: installed = metadata.version(req.name)
        except metadata.PackageNotFoundError: installed = None
        if installed is None or installed not in req.specifier:
            errors.append(f'{req.name}{req.specifier} (installed: {installed or "missing"})')
    if errors: raise RuntimeError(('旧运行环境缺少必要兼容能力：' if compatible else '运行依赖与锁文件不一致：')+'; '.join(errors))
    if imports:
        for name in ('fastapi','starlette','uvicorn','sqlalchemy','sqlglot','pydantic','psycopg2','pymysql',
                     'numpy','scipy.optimize','scipy.cluster.vq','pandas','statsmodels.api'):
            import_module(name)
    if compatible:
        # These are actual engine APIs, not every incidental transitive dependency.
        from pydantic import BaseModel, ConfigDict, field_validator, model_validator
        from sqlalchemy import create_engine, text
        from sqlglot import parse_one, exp
        from fastapi import FastAPI, Request, HTTPException
        from starlette.responses import JSONResponse
        from scipy.optimize import linprog
        from scipy.cluster.vq import kmeans2
        assert hasattr(BaseModel, 'model_validate') and hasattr(BaseModel, 'model_dump')
        assert isinstance(parse_one('SELECT 1', read='sqlite'), exp.Select)
        with create_engine('sqlite://').connect() as connection:
            assert connection.execute(text('SELECT 1')).scalar() == 1
        assert JSONResponse({'ok': True}).status_code == 200


if __name__ == '__main__':
    try: check(imports='--versions-only' not in sys.argv, compatible='--compatible' in sys.argv)
    except Exception as error: raise SystemExit(str(error)) from None
