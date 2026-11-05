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
DATE_FMT = "mm/dd/yyyy"


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


def load_sap(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, dtype={"Journal Entry": str, "Supplier": str})
    return pd.DataFrame({
        "Supplier": df["Supplier"],
        "Supplier Name": df["Supplier Name"],
        "Journal Entry Date": pd.to_datetime(df["Journal Entry Date"]),
        "Journal Entry": df["Journal Entry"],
        "Amount (CoCode Crcy)": pd.to_numeric(df["Amount (CoCode Crcy)"]),
        "System": system_name(path),
        # interne (non écrit) : écriture soldée/annulée dans SAP
        "_cleared": df["Clearing Status"].astype(str).str.strip().eq("1"),
    })


def parse_us_dates(col: pd.Series) -> pd.Series:
    """Dates exportées au format américain MM/JJ/AAAA (cas de QuickBooks).

    Si le fichier a été ouvert dans un Excel réglé en français, Excel a converti
    en vraies dates celles dont le jour est <= 12, en inversant jour et mois
    (10/01/2025 = 1er octobre devient le 10 janvier), et laissé les autres en
    texte (09/26/2025 n'existe pas en JJ/MM). On lit donc le texte en MM/JJ/AAAA
    et, quand on voit ce mélange, on remet jour et mois des vraies dates à l'endroit.
    """
    is_text = col.map(lambda v: isinstance(v, str))
    text = pd.to_datetime(col[is_text].str.strip(), format="%m/%d/%Y", errors="coerce")
    real = pd.to_datetime(col[~is_text], errors="coerce")
    known = real.dropna()
    if is_text.any() and len(known) and (known.dt.day <= 12).all():
        real = real.map(lambda d: d.replace(month=d.day, day=d.month) if pd.notna(d) else d)
    return pd.concat([text, real]).reindex(col.index)


def load_qbs(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    debit = pd.to_numeric(df["Debit"], errors="coerce").fillna(0)
    credit = pd.to_numeric(df["Credit"], errors="coerce").fillna(0)
    return pd.DataFrame({
        "Supplier": None,
        "Supplier Name": df["Name"],
        "Journal Entry Date": parse_us_dates(df["Date"]),
        "Journal Entry": df["Memo"],
        "Amount (CoCode Crcy)": (debit - credit).round(2),  # = Net (Debit - Credit)
        "System": system_name(path),
    })


def clean(value):
    return None if pd.isna(value) else value


def compute_status(rows: pd.DataFrame, sap_system: str) -> list:
    """Appariement 1 pour 1 : une ligne SAP + une ligne QBs avec le même
    deuxieme_name, la même Journal Entry Date et le même Amount (CoCode Crcy)
    (signe compris, au centime près) -> "OK" sur les deux lignes.
    Chaque ligne ne sert qu'une fois, donc nb OK SAP = nb OK QBs.
    Ensuite, lignes SAP encore vides groupées par Supplier : somme = 0 -> "Reversed".
    Puis, lignes QBs encore vides sans aucune ligne SAP de même clé -> "NOK".
    Puis, lignes SAP encore vides, par Supplier et date croissante : deux lignes
    consécutives x / -x -> "Reversed" (répété jusqu'à épuisement), le reste -> "NOK".
    Enfin, toute ligne avec Amount (CoCode Crcy) = 0 -> 0.

    Si plusieurs lignes SAP sont candidates, on prend d'abord celle non soldée.
    """
    amount = rows["Amount (CoCode Crcy)"]
    keys = list(zip(
        rows["deuxieme_name"],
        pd.to_datetime(rows["Journal Entry Date"]).dt.normalize(),
        amount.round(2),
    ))
    cleared = rows["_cleared"].fillna(False).astype(bool) if "_cleared" in rows else pd.Series(False, index=rows.index)

    sap_by_key, other_by_key = {}, {}
    for i, (k, sys_) in enumerate(zip(keys, rows["System"])):
        (sap_by_key if sys_ == sap_system else other_by_key).setdefault(k, []).append(i)

    status = [None] * len(rows)
    for k, other_idx in other_by_key.items():
        free = list(sap_by_key.get(k, []))
        for j in other_idx:
            if not free:
                break
            best = min(free, key=lambda i: (bool(cleared.iat[i]), i))
            free.remove(best)
            status[best] = status[j] = "OK"

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

    # Lignes QBs restées vides : aucune ligne SAP avec le même
    # deuxieme_name + Journal Entry Date + Amount (CoCode Crcy) -> "NOK"
    for k, other_idx in other_by_key.items():
        if k in sap_by_key:
            continue
        for j in other_idx:
            if status[j] is None:
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
    args = parser.parse_args()

    sap = load_sap(args.sap)
    qbs = load_qbs(args.qbs)
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
