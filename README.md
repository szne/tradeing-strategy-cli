# strategy-cli

Strategy Pack開発を補助するCLIです。

## インストール
```sh
pip install -e .
```

## コマンド
- `strategy new <name>`
- `strategy validate --path <strategy-pack-root>`
- `strategy test --path <strategy-pack-root>`
- `strategy backtest --engine-root <trading-engine-root>`

`strategy backtest` の `--source` は `auto/local_csv/local_parquet/http_csv/synthetic` をサポートします。

## リリース
- 手順は `RELEASE.md` を参照してください。
