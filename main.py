from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.aws.settings import AwsSettings
from src.generator.transactions_generator import generate_batch_file
from src.monitoring.operational_metrics import summarize_transactions
from src.processing.lambda_handler import process_file


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera transações e envia o lote ao S3 para processamento pela Lambda."
    )
    parser.add_argument(
        "--transactions", type=int, default=200, help="Quantidade de transações geradas."
    )
    parser.add_argument("--seed", type=int, default=42, help="Semente do gerador.")
    parser.add_argument(
        "--bucket", default=None, help="Bucket S3 de destino; sobrepõe S3_RAW_BUCKET."
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Processa o lote localmente, sem enviá-lo ao S3.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    settings = AwsSettings.from_env()
    if not args.local and not (args.bucket or settings.raw_bucket):
        raise SystemExit(
            "O processamento padrão usa AWS. Defina S3_RAW_BUCKET ou informe --bucket. "
            "Use --local para processar sem AWS."
        )

    raw_file = generate_batch_file(total_transactions=args.transactions, seed=args.seed)
    if not args.local:
        target = _upload_to_s3(raw_file, args.bucket, settings)
        print(f"Arquivo bruto:       {raw_file}")
        print(f"Publicado no S3:     {target}")
        print("A criação do objeto no S3 inicia o processamento pela Lambda.")
        return

    processed = process_file(raw_file, integrations_enabled=False)
    if processed.get("status") != "success":
        print(json.dumps(processed, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    print(f"Arquivo bruto:      {processed['input_path']}")
    print(f"Arquivo processado: {processed['output_path']}")
    print(f"Resumo operacional: {processed['summary_path']}")
    print(f"Duração:            {processed['duration_ms']} ms")
    print(
        "Registros:          "
        f"lidos={processed['records_read']} "
        f"processados={processed['records_processed']} "
        f"inválidos={processed['records_invalid']} "
        f"duplicados={processed['records_duplicated']} "
        f"alertas={processed['alerts_generated']}"
    )

    transactions = json.loads(Path(processed["output_path"]).read_text(encoding="utf-8"))
    summary = summarize_transactions(
        transactions, invalid_records=processed["records_invalid"]
    )
    print("\nDistribuição por nível de risco:")
    for level, count in summary["risk_levels"].items():
        print(f"  {level:<10} {count}")
    print("\nMotivos de alerta:")
    for reason, count in summary["alert_reasons"].items():
        print(f"  {reason:<30} {count}")
    print(f"\nRegra mais frequente: {summary['top_alert_rule']}")
    print(f"Contas com mais alertas: {summary['alerts_by_account']}")
    print(f"Alertas por data (UTC): {summary['alerts_by_period']}")


def _upload_to_s3(
    local_path: Path, bucket: str | None, settings: AwsSettings | None = None
) -> str:
    """Upload the raw batch; S3 invokes the Lambda through its object event."""
    from src.aws import s3_storage

    config = settings or AwsSettings.from_env()
    target_bucket = bucket or config.raw_bucket
    if not target_bucket:
        raise ValueError("Defina S3_RAW_BUCKET ou informe o bucket com --bucket.")
    key = s3_storage.build_key(config.raw_prefix, local_path.name)
    s3_storage.upload_file(local_path, key, bucket=target_bucket, settings=config)
    return f"s3://{target_bucket}/{key}"


if __name__ == "__main__":
    main()
