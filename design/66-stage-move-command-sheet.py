"""Generate the PowerShell 5.1 standalone capture sheet with literal paths."""
import argparse
from pathlib import Path


def ps_literal(value: Path) -> str:
    resolved = str(value.expanduser().resolve())
    return "'" + resolved.replace("'", "''") + "'"


def render(interpreter: Path, script: Path, capture: Path) -> str:
    return "\n".join([
        f"$raw  = & {ps_literal(interpreter)} {ps_literal(script)} 2>&1 | ForEach-Object {{ $_.ToString() }}",
        "$code = $LASTEXITCODE",
        "$out  = $raw | Out-String -Width 4096",
        f"Set-Content -Path {ps_literal(capture)} -Value ($out + \"`r`nEXIT_CODE=$code`r`n\") -Encoding UTF8",
        "",
    ])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interpreter", type=Path, required=True)
    p.add_argument("--script", type=Path, required=True)
    p.add_argument("--capture", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    sheet = render(a.interpreter, a.script, a.capture)
    if "«" in sheet or "»" in sheet:
        raise SystemExit("unresolved guillemet placeholder")
    a.output.write_text(sheet, encoding="utf-8")


if __name__ == "__main__":
    main()
