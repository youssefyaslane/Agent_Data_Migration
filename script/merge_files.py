"""Fusionne l'export SAP et l'export QuickBooks dans un seul fichier Excel.

Chaque ligne reçoit une colonne "System" = nom du fichier d'origine
(ex. sap.xlsx -> "SAP", QBs.xlsx -> "QBs").

deuxieme_name : nom SAP de référence. Le mapping QBs -> SAP vient du LLM
(script/name_mapping.py) et est validé dans l'app web (app/app.py).

Usage :
    python script/merge_files.py
    python script/merge_files.py --sap data/sap.xlsx --qbs data/QBs.xlsx --out output/output.xlsx
    python script/merge_files.py --mapping mapping.json
"""
import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent

COLUMNS = [
    "Supplier",
    "Supplier Name",
    "deuxieme_name",
    "Journal Entry Date",
    "Journal Entry",
    "Amount (CoCode Crcy)",
    "System",
    "Status",
    "ABS",
]

# Nom affiché dans "System" quand il diffère du nom de fichier
SYSTEM_NAMES = {"sap": "SAP"}


NUM_FMT = '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'
DATE_FMT = "dd/mm/yyyy"

# Écart de montant accepté entre une ligne SAP et une ligne QBs pour les apparier
# (ex. QBs 360 000,25 = SAP 360 000,00). Même signe obligatoire.
TOLERANCE = 3


def col(name: str) -> str:
    """Lettre Excel d'une colonne de COLUMNS."""
    return chr(ord("A") + COLUMNS.index(name))


COL_WIDTHS = {
    "Supplier": 12, "Supplier Name": 26, "deuxieme_name": 34, "Journal Entry Date": 19,
    "Journal Entry": 40, "Amount (CoCode Crcy)": 22, "System": 10, "Status": 10,
    "ABS": 16,
}


def system_name(path: Path) -> str:
    return SYSTEM_NAMES.get(path.stem.lower(), path.stem)


# Structure attendue de chaque export : sans ces colonnes, le fichier est refusé
REQUIRED_COLUMNS = {
    "SAP": ["Supplier", "Supplier Name", "Journal Entry Date", "Journal Entry",
            "Amount (CoCode Crcy)", "Clearing Status"],
    "QBs": ["Name", "Date", "Memo", "Debit", "Credit"],
}
AMOUNT_COLUMNS = {"SAP": ["Amount (CoCode Crcy)"], "QBs": ["Debit", "Credit"]}
FILE_LABELS = {"SAP": "SAP", "QBs": "QuickBooks"}


class FileCheckError(ValueError):
    """Fichier refusé : format ou structure non conforme."""


def check_file(path: Path, kind: str, **read_kw) -> pd.DataFrame:
    """Lit l'export et vérifie, avant tout calcul :
    - le format : un vrai classeur Excel .xlsx lisible ;
    - la structure : les colonnes obligatoires (REQUIRED_COLUMNS), au moins une ligne,
      des montants numériques.
    Lève FileCheckError avec un message clair sinon. Renvoie le tableau lu."""
    label = FILE_LABELS[kind]
    if Path(path).suffix.lower() != ".xlsx":
        raise FileCheckError(f"le fichier {label} doit être au format .xlsx.")
    try:
        df = pd.read_excel(path, **read_kw)
    except Exception:
        raise FileCheckError(f"le fichier {label} n’est pas un vrai fichier Excel .xlsx "
                             "(fichier abîmé, ou autre format renommé en .xlsx).") from None
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS[kind] if c not in df.columns]
    if missing:
        other = next(k for k in REQUIRED_COLUMNS if k != kind)
        if all(c in df.columns for c in REQUIRED_COLUMNS[other]):
            raise FileCheckError(f"ce fichier est un export {FILE_LABELS[other]}, pas {label} : "
                                 "les deux fichiers sont inversés.")
        s = "s" if len(missing) > 1 else ""
        raise FileCheckError(f"colonne{s} manquante{s} dans le fichier {label} : {', '.join(missing)}. "
                             f"Colonnes obligatoires : {', '.join(REQUIRED_COLUMNS[kind])}.")

    df = df.dropna(how="all")
    if df.empty:
        raise FileCheckError(f"le fichier {label} ne contient aucune ligne.")
    for c in AMOUNT_COLUMNS[kind]:
        bad = df[c].notna() & pd.to_numeric(df[c], errors="coerce").isna()
        if bad.any():
            rows = ", ".join(str(i + 2) for i in df.index[bad][:3])   # n° de ligne Excel (en-tête = 1)
            n = int(bad.sum())
            raise FileCheckError(f"colonne {c} du fichier {label} : {n} montant{'s' if n > 1 else ''} "
                                 f"non numérique{'s' if n > 1 else ''} (ligne{'s' if n > 1 else ''} "
                                 f"{rows}{'…' if n > 3 else ''}).")
    return df


def load_sap(path: Path) -> pd.DataFrame:
    df = check_file(path, "SAP", dtype={"Journal Entry": str, "Supplier": str})
    dates = parse_dates(df["Journal Entry Date"], prefer="mdy")
    out = pd.DataFrame({
        "Supplier": df["Supplier"],
        "Supplier Name": df["Supplier Name"],
        "Journal Entry Date": dates,
        "Journal Entry": df["Journal Entry"],
        "Amount (CoCode Crcy)": pd.to_numeric(df["Amount (CoCode Crcy)"]),
        "System": system_name(path),
        # interne (non écrit) : écriture soldée/annulée dans SAP
        "_cleared": df["Clearing Status"].astype(str).str.strip().eq("1"),
    })
    out.attrs["dates"] = dates.attrs["dates"]
    return out


NUM_DATE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})(?:[ T].*)?$")
ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?$")


def parse_dates(col: pd.Series, prefer: str = "mdy") -> pd.Series:
    """Lit une colonne de dates venant d'Excel, en français ou en anglais.

    - vraies dates Excel, numéros de série Excel (45992), texte ISO (2025-11-01) ;
    - texte jj/mm/aaaa (français) ou mm/jj/aaaa (anglais), séparateurs / - . :
      l'ordre est détecté sur la colonne (un 1er nombre > 12 = jour d'abord,
      un 2e nombre > 12 = mois d'abord) ; s'il reste ambigu, on prend `prefer` ;
    - fichier ouvert dans un Excel d'une autre langue : Excel a converti en vraies
      dates, jour et mois inversés, celles qu'il pouvait (jour <= 12) et laissé
      les autres en texte. On le reconnaît au mélange texte + vraies dates toutes
      avec un jour <= 12 qui, inversées, tombent dans la période du texte : on
      les remet alors à l'endroit.

    Le résultat porte un résumé dans .attrs["dates"] (affiché dans l'app).
    """
    out = pd.Series(pd.NaT, index=col.index, dtype="datetime64[ns]")
    info = {"real": 0, "serial": 0, "iso": 0, "dmy": 0, "mdy": 0, "swapped": 0, "invalid": 0, "empty": 0}
    real_idx, num_text = [], {}
    for i, v in col.items():
        if v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and not v.strip()):
            info["empty"] += 1
        elif isinstance(v, (datetime, date, pd.Timestamp)):
            out[i] = pd.Timestamp(v)
            real_idx.append(i)
        elif isinstance(v, (int, float)) and 20000 < v < 80000:      # numéro de série Excel
            out[i] = pd.Timestamp("1899-12-30") + pd.to_timedelta(int(v), unit="D")
            info["serial"] += 1
        elif isinstance(v, str) and (m := ISO_DATE.match(v.strip())):
            try:
                out[i] = pd.Timestamp(int(m[1]), int(m[2]), int(m[3]))
                info["iso"] += 1
            except ValueError:
                info["invalid"] += 1
        elif isinstance(v, str) and (m := NUM_DATE.match(v.strip())):
            num_text[i] = (int(m[1]), int(m[2]), int(m[3]) + (2000 if len(m[3]) == 2 else 0))
        else:
            info["invalid"] += 1

    # ordre jour/mois du texte, détecté sur toute la colonne
    day_first = any(a > 12 for a, _, _ in num_text.values())
    month_first = any(b > 12 for _, b, _ in num_text.values())
    order = "dmy" if day_first and not month_first else "mdy" if month_first and not day_first else None
    for i, (a, b, y) in num_text.items():
        o = order or ("dmy" if a > 12 else "mdy" if b > 12 else prefer)   # colonne mélangée : ligne par ligne
        day, month = (a, b) if o == "dmy" else (b, a)
        try:
            out[i] = pd.Timestamp(y, month, day)
            info[o] += 1
        except ValueError:
            info["invalid"] += 1
    info["order"] = order or ("mixte" if day_first else prefer)

    # vraies dates inversées par un Excel d'une autre langue
    text_dates = out[list(num_text)].dropna()
    real = out[real_idx]
    if len(text_dates) and len(real) and (real.dt.day <= 12).all():
        swapped = real.map(lambda d: d.replace(month=d.day, day=d.month))
        lo, hi = text_dates.min() - pd.Timedelta(days=45), text_dates.max() + pd.Timedelta(days=45)
        if swapped.between(lo, hi).sum() > real.between(lo, hi).sum():
            out[real_idx] = swapped
            info["swapped"] = len(real_idx)
    info["real"] = len(real_idx) - info["swapped"]
    out.attrs["dates"] = info
    return out


def describe_dates(info: dict) -> str:
    """Résumé lisible de ce que parse_dates a trouvé."""
    def n(key, one, many):
        k = info.get(key, 0)
        return f"{k} {one if k == 1 else many}" if k else None
    parts = [
        n("real", "vraie date Excel", "vraies dates Excel"),
        n("serial", "numéro de date Excel", "numéros de date Excel"),
        n("iso", "date AAAA-MM-JJ", "dates AAAA-MM-JJ"),
        n("dmy", "date en texte français (JJ/MM/AAAA)", "dates en texte français (JJ/MM/AAAA)"),
        n("mdy", "date en texte anglais (MM/JJ/AAAA)", "dates en texte anglais (MM/JJ/AAAA)"),
        n("swapped", "date inversée par Excel (jour/mois), remise à l’endroit",
          "dates inversées par Excel (jour/mois), remises à l’endroit"),
        n("invalid", "date illisible", "dates illisibles"),
        n("empty", "date vide", "dates vides"),
    ]
    return " · ".join(x for x in parts if x) or "aucune date"


def load_qbs(path: Path) -> pd.DataFrame:
    df = check_file(path, "QBs")
    debit = pd.to_numeric(df["Debit"], errors="coerce").fillna(0)
    credit = pd.to_numeric(df["Credit"], errors="coerce").fillna(0)
    dates = parse_dates(df["Date"], prefer="mdy")   # QuickBooks : anglais si ambigu
    out = pd.DataFrame({
        "Supplier": None,
        "Supplier Name": df["Name"],
        "Journal Entry Date": dates,
        "Journal Entry": df["Memo"],
        "Amount (CoCode Crcy)": (debit - credit).round(2),  # = Net (Debit - Credit)
        "System": system_name(path),
    })
    out.attrs["dates"] = dates.attrs["dates"]
    return out


def filter_period(df: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """Lignes dont la Journal Entry Date est comprise entre start et end (inclus).
    Sans start ni end : toutes les lignes. Avec une période, les lignes sans date sont exclues."""
    if start is None and end is None:
        return df
    d = pd.to_datetime(df["Journal Entry Date"]).dt.normalize()
    keep = d.notna()
    if start is not None:
        keep &= d >= pd.Timestamp(start)
    if end is not None:
        keep &= d <= pd.Timestamp(end)
    return df[keep].reset_index(drop=True)


def clean(value):
    return None if pd.isna(value) else value


def compute_status(rows: pd.DataFrame, sap_system: str, tolerance: float = TOLERANCE) -> list:
    """Appariement 1 pour 1 : une ligne SAP + une ligne QBs avec le même
    deuxieme_name, la même Journal Entry Date et un Amount (CoCode Crcy) de même
    signe avec un écart <= tolerance (3 par défaut) -> "OK" sur les deux lignes.
    Les écarts les plus petits sont appariés d'abord (montant exact en priorité).
    Chaque ligne ne sert qu'une fois, donc nb OK SAP = nb OK QBs.
    Ensuite, lignes SAP encore vides groupées par Supplier : somme = 0 -> "Reversed".
    Puis, lignes QBs encore vides sans aucune ligne SAP proche (même nom, même date,
    écart <= tolerance) -> "NOK".
    Puis, lignes SAP encore vides, par Supplier et date croissante : deux lignes
    consécutives x / -x -> "Reversed" (répété jusqu'à épuisement), le reste -> "NOK".
    Enfin, toute ligne avec Amount (CoCode Crcy) = 0 -> 0.

    Si plusieurs lignes SAP sont candidates au même écart, on prend d'abord celle non soldée.
    """
    amount = rows["Amount (CoCode Crcy)"]
    amt2 = amount.round(2)
    keys = list(zip(
        rows["deuxieme_name"],
        pd.to_datetime(rows["Journal Entry Date"]).dt.normalize(),
    ))
    cleared = rows["_cleared"].fillna(False).astype(bool) if "_cleared" in rows else pd.Series(False, index=rows.index)

    sap_by_key, other_by_key = {}, {}
    for i, (k, sys_) in enumerate(zip(keys, rows["System"])):
        (sap_by_key if sys_ == sap_system else other_by_key).setdefault(k, []).append(i)

    def close(i, j):
        """Même signe et écart <= tolerance (au centime près)."""
        a, b = amt2.iat[i], amt2.iat[j]
        return (pd.notna(a) and pd.notna(b) and (a > 0) == (b > 0) and (a < 0) == (b < 0)
                and round(abs(a - b), 2) <= tolerance)

    status = [None] * len(rows)
    for k, other_idx in other_by_key.items():
        pairs = sorted(
            (round(abs(amt2.iat[i] - amt2.iat[j]), 2), bool(cleared.iat[i]), j, i)
            for j in other_idx for i in sap_by_key.get(k, []) if close(i, j))
        used = set()
        for _, _, j, i in pairs:
            if i not in used and j not in used:
                used.update((i, j))
                status[i] = status[j] = "OK"

    # Lignes SAP restées vides : groupées par Supplier,
    # si la somme de Amount (CoCode Crcy) = 0 -> "Reversed"
    empty_sap = [i for idx in sap_by_key.values() for i in idx if status[i] is None]
    by_supplier = {}
    for i in empty_sap:
        by_supplier.setdefault(rows["Supplier"].iat[i], []).append(i)
    for supplier, idx in by_supplier.items():
        if pd.notna(supplier) and round(sum(amount.iat[i] for i in idx), 2) == 0:
            for i in idx:
                status[i] = "Reversed"

    # Lignes QBs restées vides : aucune ligne SAP avec le même deuxieme_name,
    # la même Journal Entry Date et un montant à <= tolerance près -> "NOK"
    for k, other_idx in other_by_key.items():
        for j in other_idx:
            if status[j] is None and not any(close(i, j) for i in sap_by_key.get(k, [])):
                status[j] = "NOK"

    # Lignes SAP encore vides : par Supplier, triées par date croissante.
    # Pour chaque ligne x, on cherche dans TOUTES les autres lignes du Supplier
    # (la plus proche en date d'abord) une ligne de montant -x.
    # Trouvée -> "Reversed" sur les deux, on les retire et on recommence
    # jusqu'à ce qu'il n'y ait plus de paire. Le reste -> "NOK".
    dates = pd.to_datetime(rows["Journal Entry Date"])
    entries = rows["Journal Entry"].astype(str)
    by_supplier = {}
    for idx in sap_by_key.values():
        for i in idx:
            if status[i] is None:
                by_supplier.setdefault(rows["Supplier"].iat[i], []).append(i)
    for idx in by_supplier.values():
        lines = sorted(idx, key=lambda i: (dates.iat[i], entries.iat[i]))
        changed = True
        while changed:
            changed = False
            for a in lines:
                b = next((b for b in lines
                          if b != a and round(amount.iat[a] + amount.iat[b], 2) == 0), None)
                if b is not None:
                    status[a] = status[b] = "Reversed"
                    lines.remove(a)
                    lines.remove(b)
                    changed = True
                    break
        for i in lines:
            status[i] = "NOK"

    # Dernière règle : Amount (CoCode Crcy) = 0 -> Status 0 (SAP et QBs),
    # quel que soit le statut trouvé avant.
    for i in range(len(rows)):
        if pd.notna(amount.iat[i]) and round(amount.iat[i], 2) == 0:
            status[i] = 0
    return status


def deuxieme_name(rows: pd.DataFrame, sap_system: str, mapping: dict,
                  sap_aliases: dict | None = None) -> pd.Series:
    """SAP : son propre Supplier Name (ou la fiche SAP principale si c'est un doublon).
    QBs : le nom SAP choisi dans le mapping (validé dans l'app ou proposé par le LLM),
    sinon son propre nom."""
    sap_aliases = sap_aliases or {}

    def canonical(name):
        seen = set()
        while sap_aliases.get(name) and name not in seen:   # suit A -> B -> C, sans boucle
            seen.add(name)
            name = sap_aliases[name]
        return name

    def one(name, system):
        if pd.isna(name):
            return None
        name = str(name).strip()
        if system == sap_system:
            return canonical(name)
        return canonical(mapping.get(name) or name)
    return pd.Series([one(n, s) for n, s in zip(rows["Supplier Name"], rows["System"])],
                     index=rows.index)


def write_output(sap: pd.DataFrame, qbs: pd.DataFrame, out: Path, mapping: dict,
                 sap_aliases: dict | None = None) -> None:
    """mapping = {nom QBs: nom SAP ou None}
    sap_aliases = {nom SAP en double: nom SAP principal ou None}."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="F7F7F7")
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill

    sap_system = sap["System"].iloc[0] if len(sap) else system_name(Path("sap"))
    rows = pd.concat([sap, qbs], ignore_index=True)
    rows.insert(2, "deuxieme_name", deuxieme_name(rows, sap_system, mapping, sap_aliases))
    status = compute_status(rows, sap_system)

    amt, abs_ = col("Amount (CoCode Crcy)"), col("ABS")
    num_cols = (amt, abs_)
    visible = COLUMNS[:COLUMNS.index("System") + 1]   # Supplier ... System

    for i, rec in enumerate(rows[visible].itertuples(index=False), start=2):
        values = [clean(v) for v in rec]
        ws.append(values + [
            status[i - 2],                                          # Status
            f"=+ABS({amt}{i})",                                     # ABS
        ])
        ws[f"{col('Journal Entry Date')}{i}"].number_format = DATE_FMT
        for c in num_cols:
            ws[f"{c}{i}"].number_format = NUM_FMT

    for c in num_cols:
        ws[f"{c}1"].number_format = NUM_FMT
    for name, width in COL_WIDTHS.items():
        ws.column_dimensions[col(name)].width = width
    ws.freeze_panes = "A2"

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)


def main():
    parser = argparse.ArgumentParser(description="Fusion SAP + QBs")
    parser.add_argument("--sap", type=Path, default=ROOT / "data" / "sap.xlsx")
    parser.add_argument("--qbs", type=Path, default=ROOT / "data" / "QBs.xlsx")
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "output.xlsx")
    parser.add_argument("--mapping", type=Path,
                        help='JSON {"qbs": {nom QBs: nom SAP}, "sap": {doublon SAP: nom SAP}}. '
                             "Sinon : noms enregistrés + LLM pour les nouveaux (sans validation).")
    parser.add_argument("--du", help="début de la période (AAAA-MM-JJ), d'après Journal Entry Date")
    parser.add_argument("--au", help="fin de la période (AAAA-MM-JJ), incluse")
    args = parser.parse_args()

    sap = filter_period(load_sap(args.sap), args.du, args.au)
    qbs = filter_period(load_qbs(args.qbs), args.du, args.au)
    if args.mapping:
        m = json.loads(args.mapping.read_text(encoding="utf-8"))
        mapping, sap_aliases = m.get("qbs", {}), m.get("sap", {})
    else:
        from name_mapping import suggest_mapping, suggest_sap_aliases, unique_names
        sap_names = unique_names(sap["Supplier Name"])
        show = lambda what: lambda i, n, name: print(f"\r  {what} {i}/{n}", end="", flush=True)
        sugg = suggest_mapping(sap_names, unique_names(qbs["Supplier Name"]), show("mapping QBs"))
        print()
        mapping = {q: s["sap"] for q, s in sugg.items()}
        alias = suggest_sap_aliases(sap_names, {v for v in mapping.values() if v}, show("doublons SAP"))
        print()
        sap_aliases = {k: s["sap"] for k, s in alias.items()}
        for kind, d in (("QBs", sugg), ("SAP", alias)):
            for name, s in d.items():
                if s["source"] == "LLM":
                    print(f"  LLM {kind} : {name!r} -> {s['sap']!r}")
    write_output(sap, qbs, args.out, mapping, sap_aliases)
    print(f"{len(sap)} lignes {system_name(args.sap)} + {len(qbs)} lignes "
          f"{system_name(args.qbs)} -> {args.out}")


if __name__ == "__main__":
    main()
