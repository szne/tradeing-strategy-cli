from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from textwrap import dedent
from typing import Iterable, List

DEFAULT_PACKAGE = "strategy_pack"
DEFAULT_ENTRYPOINT_GROUP = "trading_system.strategies"


def snake_case(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    if not normalized:
        raise ValueError("strategy name must include alphanumeric characters")
    if normalized[0].isdigit():
        raise ValueError("strategy name must not start with a digit")
    return normalized


def pascal_case(name: str) -> str:
    return "".join(part.capitalize() for part in snake_case(name).split("_"))


def strategy_template(class_name: str) -> str:
    return dedent(
        f"""
        from __future__ import annotations

        from typing import Any, Dict

        from trading_sdk.base_strategy import BaseStrategy
        from trading_sdk.structs import AccountSnapshot, OrderSignal


        class {class_name}(BaseStrategy):
            def default_params(self) -> Dict[str, Any]:
                return {{"risk_pct": 0.01, "min_qty": 0.0}}

            def next_signal(self, market_data: Any, account_data: Any) -> OrderSignal:
                if market_data is None or "Close" not in market_data.columns:
                    return OrderSignal(
                        action="WAIT",
                        quantity=0.0,
                        reasoning="insufficient data",
                    )
                if len(market_data) < 2:
                    return OrderSignal(
                        action="WAIT",
                        quantity=0.0,
                        reasoning="insufficient data",
                    )

                close = market_data["Close"]
                prev_price = float(close.iloc[-2])
                curr_price = float(close.iloc[-1])
                if curr_price <= 0:
                    return OrderSignal(action="WAIT", quantity=0.0, reasoning="invalid price")

                snapshot = AccountSnapshot.from_account_data(account_data)
                balance = snapshot.cash if snapshot.cash > 0 else snapshot.balance
                risk_pct = float(self.params.get("risk_pct", 0.0))
                if balance <= 0 or risk_pct <= 0:
                    return OrderSignal(
                        action="WAIT",
                        quantity=0.0,
                        reasoning="invalid account or params",
                    )

                quantity = (balance * risk_pct) / curr_price
                min_qty = float(self.params.get("min_qty", 0.0))
                if quantity <= min_qty:
                    return OrderSignal(
                        action="WAIT",
                        quantity=0.0,
                        reasoning="quantity below minimum",
                    )

                action = "BUY" if curr_price > prev_price else "SELL"
                reason = "price up" if action == "BUY" else "price down"
                return OrderSignal(
                    action=action,
                    quantity=quantity,
                    type="MARKET",
                    reasoning=reason,
                )
        """
    ).strip() + "\n"


def strategy_test_template(module_name: str, class_name: str) -> str:
    return dedent(
        f"""
        from __future__ import annotations

        import pandas as pd

        from {DEFAULT_PACKAGE}.strategies.{module_name} import {class_name}


        def test_strategy_returns_order_signal() -> None:
            strategy = {class_name}(params={{"risk_pct": 0.01}})
            strategy.setup({{"backtest": {{"fee_rate": 0.001}}}})
            market = pd.DataFrame({{"Close": [100.0, 101.0]}})
            signal = strategy.next_signal(
                market,
                {{"balance": 1000.0, "cash": 1000.0}},
            )
            assert signal.action in {{"BUY", "SELL", "WAIT"}}
        """
    ).strip() + "\n"


def ensure_init_file(path: Path) -> None:
    if not path.exists():
        path.write_text("", encoding="utf-8")


def cmd_new(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    package = args.package
    module_name = snake_case(args.name)
    class_name = args.class_name or f"{pascal_case(module_name)}Strategy"

    package_dir = root / package
    strategy_dir = package_dir / "strategies"
    tests_dir = root / "tests"
    strategy_file = strategy_dir / f"{module_name}.py"
    test_file = tests_dir / f"test_{module_name}.py"

    for directory in (package_dir, strategy_dir, tests_dir):
        directory.mkdir(parents=True, exist_ok=True)

    ensure_init_file(package_dir / "__init__.py")
    ensure_init_file(strategy_dir / "__init__.py")

    if strategy_file.exists() and not args.force:
        print(f"strategy file already exists: {strategy_file}", file=sys.stderr)
        return 1
    if test_file.exists() and not args.force:
        print(f"test file already exists: {test_file}", file=sys.stderr)
        return 1

    strategy_file.write_text(strategy_template(class_name), encoding="utf-8")
    test_file.write_text(strategy_test_template(module_name, class_name), encoding="utf-8")

    print(f"created strategy: {strategy_file}")
    print(f"created test: {test_file}")
    print(
        "next: add entrypoint in pyproject.toml under "
        f"[project.entry-points.\"{DEFAULT_ENTRYPOINT_GROUP}\"]"
    )
    return 0


def _parse_import_violations(path: Path) -> List[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations: List[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ccxt" or alias.name.startswith("ccxt."):
                    violations.append(f"{path}: forbidden import '{alias.name}'")
                if alias.name.startswith("core"):
                    violations.append(f"{path}: engine dependency import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "ccxt" or module.startswith("ccxt."):
                violations.append(f"{path}: forbidden import from '{module}'")
            if module == "core" or module.startswith("core."):
                violations.append(f"{path}: engine dependency import from '{module}'")

    return violations


def _parse_strategy_classes(path: Path) -> List[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    issues: List[str] = []

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_names.append(base.id)
            elif isinstance(base, ast.Attribute):
                base_names.append(base.attr)
        if "BaseStrategy" not in base_names:
            continue
        method_names = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
        if "next_signal" not in method_names:
            issues.append(f"{path}: {node.name} must define next_signal")
    return issues


def _validate_pyproject(path: Path, package: str) -> List[str]:
    issues: List[str] = []
    pyproject = path / "pyproject.toml"
    if not pyproject.exists():
        return [f"{pyproject}: missing"]

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    dependencies = project.get("dependencies", [])
    if not any(str(dep).startswith("trading-sdk") for dep in dependencies):
        issues.append(f"{pyproject}: dependency 'trading-sdk' is required")

    entrypoints = project.get("entry-points", {})
    group = entrypoints.get(DEFAULT_ENTRYPOINT_GROUP, {})
    if not group:
        issues.append(
            f"{pyproject}: entrypoint group '{DEFAULT_ENTRYPOINT_GROUP}' has no registrations"
        )
    else:
        for name, target in group.items():
            if ":" not in str(target):
                issues.append(f"{pyproject}: invalid entrypoint target for '{name}'")
                continue
            module_name = str(target).split(":", 1)[0]
            module_path = path / (module_name.replace(".", "/") + ".py")
            if not module_path.exists():
                issues.append(f"{pyproject}: entrypoint module not found '{module_path}'")
            if not module_name.startswith(package):
                issues.append(
                    f"{pyproject}: entrypoint '{name}' should be under package '{package}'"
                )

    return issues


def cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    package_dir = root / args.package
    if not package_dir.exists():
        print(f"package directory not found: {package_dir}", file=sys.stderr)
        return 1

    issues: List[str] = []
    for path in package_dir.rglob("*.py"):
        issues.extend(_parse_import_violations(path))
        issues.extend(_parse_strategy_classes(path))

    issues.extend(_validate_pyproject(root, args.package))

    if issues:
        print("validation failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print("validation passed")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    cmd = [sys.executable, "-m", "pytest", *args.pytest_args]
    return subprocess.run(cmd, cwd=root, check=False).returncode


def cmd_backtest(args: argparse.Namespace) -> int:
    engine_root = Path(args.engine_root).resolve()
    command = [
        sys.executable,
        "-m",
        "runners.backtest_runner",
        args.config,
        "--source",
        args.source,
        "--rows",
        str(args.rows),
    ]
    if args.strategy:
        command.extend(["--strategy", args.strategy])
    return subprocess.run(command, cwd=engine_root, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strategy Pack development CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="create strategy and test templates")
    new_parser.add_argument("name", help="strategy name (snake case or free text)")
    new_parser.add_argument("--path", default=".", help="strategy-pack root")
    new_parser.add_argument("--package", default=DEFAULT_PACKAGE, help="python package name")
    new_parser.add_argument("--class-name", dest="class_name", default=None)
    new_parser.add_argument("--force", action="store_true", help="overwrite existing files")
    new_parser.set_defaults(func=cmd_new)

    validate_parser = subparsers.add_parser("validate", help="validate strategy-pack constraints")
    validate_parser.add_argument("--path", default=".", help="strategy-pack root")
    validate_parser.add_argument("--package", default=DEFAULT_PACKAGE, help="python package name")
    validate_parser.set_defaults(func=cmd_validate)

    test_parser = subparsers.add_parser("test", help="run strategy-pack tests")
    test_parser.add_argument("--path", default=".", help="strategy-pack root")
    test_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    test_parser.set_defaults(func=cmd_test)

    backtest_parser = subparsers.add_parser("backtest", help="run engine backtest command")
    backtest_parser.add_argument("--engine-root", default="..", help="engine repository root")
    backtest_parser.add_argument(
        "--config",
        default="configs/default.yaml",
        help="engine config path",
    )
    backtest_parser.add_argument(
        "--source",
        default="synthetic",
        choices=["auto", "local_csv", "local_parquet", "http_csv", "synthetic"],
    )
    backtest_parser.add_argument("--rows", type=int, default=500)
    backtest_parser.add_argument("--strategy", default=None)
    backtest_parser.set_defaults(func=cmd_backtest)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
