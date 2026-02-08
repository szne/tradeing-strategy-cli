# ruff: noqa: E402

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[1]
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

from strategy_cli.cli import cmd_new, cmd_validate, snake_case


def test_snake_case_normalizes_text() -> None:
    assert snake_case("My Strategy") == "my_strategy"


def test_cmd_new_creates_strategy_and_test_files(tmp_path: Path) -> None:
    args = Namespace(
        name="alpha edge",
        path=str(tmp_path),
        package="strategy_pack",
        class_name=None,
        force=False,
    )

    code = cmd_new(args)

    strategy_path = tmp_path / "strategy_pack" / "strategies" / "alpha_edge.py"
    test_path = tmp_path / "tests" / "test_alpha_edge.py"

    assert code == 0
    assert strategy_path.exists()
    assert test_path.exists()


def test_cmd_validate_rejects_forbidden_imports(tmp_path: Path) -> None:
    package_dir = tmp_path / "strategy_pack" / "strategies"
    package_dir.mkdir(parents=True)
    (tmp_path / "strategy_pack" / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "bad.py").write_text(
        "import ccxt\n"
        "from trading_sdk.base_strategy import BaseStrategy\n"
        "class BadStrategy(BaseStrategy):\n"
        "    def setup(self, config):\n"
        "        self.config = config\n"
        "    def set_adapter(self, adapter):\n"
        "        self.adapter = adapter\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        "name='strategy-pack'\n"
        "version='0.1.0'\n"
        "dependencies=['trading-sdk>=0.1.0']\n"
        "[project.entry-points.\"trading_system.strategies\"]\n"
        "bad='strategy_pack.strategies.bad:BadStrategy'\n",
        encoding="utf-8",
    )

    args = Namespace(path=str(tmp_path), package="strategy_pack")

    code = cmd_validate(args)

    assert code == 1


def test_cmd_validate_passes_for_valid_package(tmp_path: Path) -> None:
    package_dir = tmp_path / "strategy_pack" / "strategies"
    package_dir.mkdir(parents=True)
    (tmp_path / "strategy_pack" / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "ok.py").write_text(
        "from trading_sdk.base_strategy import BaseStrategy\n"
        "from trading_sdk.structs import OrderSignal\n"
        "class OkStrategy(BaseStrategy):\n"
        "    def setup(self, config):\n"
        "        self.config = config\n"
        "    def set_adapter(self, adapter):\n"
        "        self.adapter = adapter\n"
        "    def next_signal(self, market_data, account_data):\n"
        "        return OrderSignal(action='WAIT', quantity=0.0)\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        "name='strategy-pack'\n"
        "version='0.1.0'\n"
        "dependencies=['trading-sdk>=0.1.0']\n"
        "[project.entry-points.\"trading_system.strategies\"]\n"
        "ok='strategy_pack.strategies.ok:OkStrategy'\n",
        encoding="utf-8",
    )

    args = Namespace(path=str(tmp_path), package="strategy_pack")

    code = cmd_validate(args)

    assert code == 0
