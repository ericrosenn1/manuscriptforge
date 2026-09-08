from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from manuscriptforge.models.claim import EvidenceItem
from manuscriptforge.models.project import TableSchema
from manuscriptforge.utils.hashing import sha256_file, sha256_text
from manuscriptforge.utils.ids import stable_id


class TableIngestError(ValueError):
    """Raised when a user-supplied result table cannot be read safely."""


def _records_from_frame(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a cleaned DataFrame into JSON-safe row dictionaries."""
    # pandas can emit numpy scalars and NaN; JSON round-tripping produces plain Python values.
    return json.loads(df.where(pd.notnull(df), None).to_json(orient="records"))


def read_table(path: Path) -> pd.DataFrame:
    """Read a supported result table or raise a user-facing ingest error."""
    try:
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path, encoding="utf-8-sig", on_bad_lines="error")
        if path.suffix.lower() in {".xlsx", ".xls"}:
            return pd.read_excel(path)
    except Exception as exc:
        raise TableIngestError(f"Could not read result table {path}: {exc}") from exc
    raise TableIngestError(f"Unsupported table format for {path}; use CSV or XLSX")


def _normalize_column_name(column: Any, index: int) -> str:
    """Replace blank or unnamed columns with stable generated names."""
    name = str(column).strip()
    if not name or name.lower().startswith("unnamed:"):
        return f"column_{index}"
    return name


def _clean_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Normalize headers and drop fully empty rows or columns."""
    warnings: list[str] = []
    if df.empty and len(df.columns) == 0:
        warnings.append("table has no columns")
        return df, warnings
    normalized_columns = [_normalize_column_name(column, index) for index, column in enumerate(df.columns, start=1)]
    if normalized_columns != [str(column) for column in df.columns]:
        warnings.append("blank or unnamed columns were normalized")
    df = df.copy()
    df.columns = normalized_columns
    before_rows = len(df)
    before_cols = len(df.columns)
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    if len(df) != before_rows:
        warnings.append(f"dropped {before_rows - len(df)} empty row(s)")
    if len(df.columns) != before_cols:
        warnings.append(f"dropped {before_cols - len(df.columns)} empty column(s)")
    duplicate_columns = [column for column in df.columns if list(df.columns).count(column) > 1]
    if duplicate_columns:
        warnings.append(f"duplicate columns detected: {', '.join(sorted(set(duplicate_columns)))}")
    return df, warnings


def _coerce_numeric_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Convert mostly numeric text columns and report what changed."""
    df = df.copy()
    numeric_columns: list[str] = []
    warnings: list[str] = []
    for column in df.columns:
        if pd.api.types.is_numeric_dtype(df[column]):
            numeric_columns.append(str(column))
            continue
        coerced = pd.to_numeric(df[column], errors="coerce")
        non_null_original = df[column].notna().sum()
        non_null_coerced = coerced.notna().sum()
        if non_null_original and non_null_coerced / non_null_original >= 0.8:
            df[column] = coerced
            numeric_columns.append(str(column))
            warnings.append(f"coerced numeric-like column '{column}'")
    return df, numeric_columns, warnings


def _schema_for(path: Path, schemas: dict[str, TableSchema] | None) -> TableSchema | None:
    """Find a configured table schema by filename."""
    if not schemas:
        return None
    return schemas.get(path.name)


def _role_columns(df: pd.DataFrame, schema: TableSchema | None) -> dict[str, list[str]]:
    """Map schema roles to columns that exist in the DataFrame."""
    role_columns: dict[str, list[str]] = {}
    if schema is None:
        return role_columns
    for column, role in schema.column_roles.items():
        if column in df.columns:
            role_columns.setdefault(role, []).append(column)
    return role_columns


def _missingness_summary(df: pd.DataFrame) -> dict[str, dict[str, float | int]]:
    """Summarize missing values for every table column."""
    summary: dict[str, dict[str, float | int]] = {}
    row_count = max(len(df), 1)
    for column in df.columns:
        missing = int(df[column].isna().sum())
        summary[str(column)] = {
            "missing_count": missing,
            "missing_fraction": round(missing / row_count, 4),
        }
    return summary


def _columns_for_role(role_columns: dict[str, list[str]], roles: list[str]) -> list[str]:
    """Flatten configured columns for one or more semantic roles."""
    found: list[str] = []
    for role in roles:
        found.extend(role_columns.get(role, []))
    return found


def _detected_special_columns(
    df: pd.DataFrame, numeric_columns: list[str], role_columns: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Detect p-value, adjusted p-value, and effect-size columns."""
    lower_map = {column.lower(): column for column in df.columns}
    p_value = _columns_for_role(role_columns, ["p_value"])
    adjusted = _columns_for_role(role_columns, ["adjusted_p_value", "q_value", "fdr"])
    effect = _columns_for_role(role_columns, ["effect_size", "estimate", "fold_change"])
    if not p_value:
        p_value = [lower_map[key] for key in ["p_value", "pvalue", "p"] if key in lower_map]
    if not adjusted:
        adjusted = [
            lower_map[key]
            for key in ["adjusted_p_value", "padj", "q_value", "qvalue", "fdr", "p_adj"]
            if key in lower_map
        ]
    if not effect:
        effect = [
            lower_map[key]
            for key in ["log2_fold_change", "log2fc", "fold_change", "effect_size", "estimate"]
            if key in lower_map
        ]
    return {
        "p_value_columns": [column for column in p_value if column in numeric_columns],
        "adjusted_p_value_columns": [column for column in adjusted if column in numeric_columns],
        "effect_size_columns": [column for column in effect if column in numeric_columns],
    }


def _threshold_counts(
    df: pd.DataFrame,
    thresholds: dict[str, float],
    role_columns: dict[str, list[str]],
    special_columns: dict[str, list[str]],
) -> dict[str, dict[str, float | int]]:
    """Count rows meeting configured thresholds for special columns."""
    counts: dict[str, dict[str, float | int]] = {}
    for configured_column, threshold in thresholds.items():
        candidate_columns = [configured_column]
        candidate_columns.extend(role_columns.get(configured_column, []))
        if configured_column in {"p_value", "adjusted_p_value"}:
            candidate_columns.extend(special_columns.get(f"{configured_column}_columns", []))
        for column in dict.fromkeys(candidate_columns):
            if column in df.columns:
                numeric = pd.to_numeric(df[column], errors="coerce")
                counts[column] = {
                    "threshold": threshold,
                    "count_at_or_below": int((numeric <= threshold).sum()),
                    "non_null_count": int(numeric.notna().sum()),
                }
    return counts


def _top_effects(df: pd.DataFrame, effect_columns: list[str], limit: int = 5) -> list[dict[str, Any]]:
    """Return the largest absolute effect rows across effect columns."""
    effects: list[dict[str, Any]] = []
    for column in effect_columns:
        numeric = pd.to_numeric(df[column], errors="coerce")
        for index, value in numeric.abs().sort_values(ascending=False).head(limit).items():
            if pd.isna(value):
                continue
            row = df.loc[index].to_dict()
            effects.append(
                {
                    "column": column,
                    "row_index": int(index) + 1 if isinstance(index, int) else str(index),
                    "absolute_value": float(value),
                    "value": row.get(column),
                    "row": json.loads(pd.Series(row).to_json()),
                }
            )
    return sorted(effects, key=lambda item: item["absolute_value"], reverse=True)[:limit]


def ingest_result_tables(
    project_dir: Path, schemas: dict[str, TableSchema] | None = None
) -> tuple[list[dict[str, Any]], list[EvidenceItem]]:
    """Ingest result tables into summaries and evidence items."""
    tables_dir = project_dir / "inputs" / "results_tables"
    summaries: list[dict[str, Any]] = []
    evidence_items: list[EvidenceItem] = []
    if not tables_dir.exists():
        return summaries, evidence_items

    for path in sorted(list(tables_dir.glob("*.csv")) + list(tables_dir.glob("*.xlsx"))):
        df = read_table(path)
        df, cleaning_warnings = _clean_frame(df)
        df, numeric_columns, numeric_warnings = _coerce_numeric_columns(df)
        schema = _schema_for(path, schemas)
        role_columns = _role_columns(df, schema)
        special_columns = _detected_special_columns(df, numeric_columns, role_columns)
        threshold_counts = _threshold_counts(
            df,
            schema.thresholds if schema else {},
            role_columns,
            special_columns,
        )
        top_effects = _top_effects(df, special_columns["effect_size_columns"])
        records = _records_from_frame(df)
        rel_path = path.relative_to(project_dir).as_posix()
        file_hash = sha256_file(path)
        table_id = stable_id("tbl", rel_path + file_hash)
        table_warnings = cleaning_warnings + numeric_warnings
        if len(records) == 0:
            table_warnings.append("table has no data rows after cleaning")
        summary = {
            "table_id": table_id,
            "source_path": rel_path,
            "source_label": path.stem,
            "columns": [str(column) for column in df.columns],
            "column_count": int(len(df.columns)),
            "numeric_columns": [str(column) for column in numeric_columns],
            "missingness": _missingness_summary(df),
            "role_columns": role_columns,
            "schema": schema.model_dump(mode="json") if schema else None,
            "p_value_columns": special_columns["p_value_columns"],
            "adjusted_p_value_columns": special_columns["adjusted_p_value_columns"],
            "effect_size_columns": special_columns["effect_size_columns"],
            "threshold_counts": threshold_counts,
            "top_effects": top_effects,
            "row_count": int(len(df)),
            "rows": records,
            "warnings": table_warnings,
            "content_hash": file_hash,
        }
        summaries.append(summary)
        text = (
            f"Table {path.stem} contains {len(df)} row(s), columns "
            f"{', '.join(map(str, df.columns))}, and numeric columns "
            f"{', '.join(map(str, numeric_columns)) or 'none'}."
        )
        evidence_items.append(
            EvidenceItem(
                evidence_id=stable_id("ev", text + file_hash),
                evidence_type="table",
                source_path=rel_path,
                source_label=path.stem,
                locator="table_summary",
                text=text,
                metadata=summary,
                content_hash=sha256_text(text + file_hash),
            )
        )
        for index, row in enumerate(records[:50], start=1):
            row_text = f"Row {index} in {path.stem}: " + "; ".join(
                f"{key}={value}" for key, value in row.items()
            )
            evidence_items.append(
                EvidenceItem(
                    evidence_id=stable_id("ev", row_text + file_hash),
                    evidence_type="table",
                    source_path=rel_path,
                    source_label=path.stem,
                    locator=f"row {index}",
                    text=row_text,
                    metadata={"table_id": table_id, "row_index": index, "row": row},
                    content_hash=sha256_text(row_text + file_hash),
                )
            )
    return summaries, evidence_items
