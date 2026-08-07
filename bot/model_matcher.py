"""Сопоставление найденной модели товара с требованиями ТЗ."""

from __future__ import annotations

import re
from typing import Any


STATUS_MATCH = "MATCH"
STATUS_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_REJECTED = "REJECTED"

_NUMBER_RE = re.compile(r"[-+]?\d+(?:[\.,]\d+)?")


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUMBER_RE.search(str(value).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _norm_unit(unit: str | None) -> str:
    if not unit:
        return ""
    u = str(unit).strip().lower().replace("²", "2").replace("³", "3")
    aliases = {
        "мм": "mm", "миллиметр": "mm", "миллиметров": "mm",
        "см": "cm", "м": "m", "метр": "m", "метров": "m",
        "кг": "kg", "г": "g", "л": "l", "литр": "l",
        "шт": "pcs", "шт.": "pcs", "ед": "pcs", "ед.": "pcs",
        "вт": "w", "квт": "kw", "м3/ч": "m3h", "м³/ч": "m3h",
        "м3/час": "m3h", "м³/час": "m3h", "л/мин": "lmin",
        "об/мин": "rpm", "гц": "hz", "в": "v", "а": "a",
    }
    return aliases.get(u, u)


def _convert(value: float, unit: str, target: str) -> float | None:
    u, t = _norm_unit(unit), _norm_unit(target)
    if u == t or not t:
        return value
    conversions = {
        ("mm", "cm"): 0.1,
        ("mm", "m"): 0.001,
        ("cm", "mm"): 10.0,
        ("cm", "m"): 0.01,
        ("m", "mm"): 1000.0,
        ("m", "cm"): 100.0,
        ("g", "kg"): 0.001,
        ("kg", "g"): 1000.0,
        ("w", "kw"): 0.001,
        ("kw", "w"): 1000.0,
    }
    factor = conversions.get((u, t))
    return value * factor if factor is not None else None


def _extract_spec(model_specs: dict, parameter: str) -> tuple[float | None, str | None, str | None]:
    """Ищет характеристику по точному имени, затем по нормализованному ключу."""
    if not isinstance(model_specs, dict):
        return None, None, None

    direct = model_specs.get(parameter)
    if direct is None:
        normalized = re.sub(r"[^a-zа-я0-9]+", "", parameter.lower())
        for key, value in model_specs.items():
            nk = re.sub(r"[^a-zа-я0-9]+", "", str(key).lower())
            if nk == normalized or normalized in nk or nk in normalized:
                direct = value
                break

    if isinstance(direct, dict):
        value = direct.get("value")
        unit = direct.get("unit")
        raw = direct.get("raw")
    else:
        value, unit, raw = direct, None, str(direct) if direct is not None else None

    return _number(value), unit, raw


def compare_requirement(requirement: dict, model_specs: dict) -> dict:
    parameter = str(requirement.get("parameter") or requirement.get("name") or "").strip()
    operator = str(requirement.get("operator") or "=").upper().strip()
    required_value = requirement.get("value")
    required_min = requirement.get("min")
    required_max = requirement.get("max")
    required_unit = requirement.get("unit")

    actual, actual_unit, actual_raw = _extract_spec(model_specs, parameter)
    if actual is None:
        return {
            "status": STATUS_UNKNOWN,
            "parameter": parameter,
            "reason": "Характеристика модели не подтверждена источником.",
            "required": requirement,
            "actual": actual_raw,
        }

    if required_min is not None or required_max is not None or operator == "RANGE":
        lo = _number(required_min)
        hi = _number(required_max)
        if lo is not None:
            lo2 = _convert(lo, required_unit or "", actual_unit or required_unit or "")
            if lo2 is None:
                return _unknown_unit(parameter, requirement, actual, actual_unit)
        else:
            lo2 = None
        if hi is not None:
            hi2 = _convert(hi, required_unit or "", actual_unit or required_unit or "")
            if hi2 is None:
                return _unknown_unit(parameter, requirement, actual, actual_unit)
        else:
            hi2 = None
        ok = (lo2 is None or actual >= lo2) and (hi2 is None or actual <= hi2)
    else:
        required = _number(required_value)
        if required is None:
            return {
                "status": STATUS_UNKNOWN,
                "parameter": parameter,
                "reason": "Не удалось распознать числовое требование.",
                "required": requirement,
                "actual": actual_raw,
            }
        converted = _convert(required, required_unit or "", actual_unit or required_unit or "")
        if converted is None:
            return _unknown_unit(parameter, requirement, actual, actual_unit)
        if operator in ("=", "EQ"):
            ok = actual == converted
        elif operator == ">":
            ok = actual > converted
        elif operator == ">=":
            ok = actual >= converted
        elif operator == "<":
            ok = actual < converted
        elif operator == "<=":
            ok = actual <= converted
        else:
            return {
                "status": STATUS_UNKNOWN,
                "parameter": parameter,
                "reason": f"Неподдерживаемый оператор: {operator}",
                "required": requirement,
                "actual": actual_raw,
            }

    return {
        "status": STATUS_MATCH if ok else STATUS_REJECTED,
        "parameter": parameter,
        "reason": "Требование выполнено." if ok else "Значение характеристики не соответствует ТЗ.",
        "required": requirement,
        "actual": {"value": actual, "unit": actual_unit, "raw": actual_raw},
    }


def _unknown_unit(parameter: str, requirement: dict, actual: float, actual_unit: str | None) -> dict:
    return {
        "status": STATUS_NEEDS_CLARIFICATION,
        "parameter": parameter,
        "reason": "Нельзя безопасно сравнить единицы измерения автоматически.",
        "required": requirement,
        "actual": {"value": actual, "unit": actual_unit},
    }


def match_model(requirements: list[dict], model_specs: dict) -> dict:
    results = [compare_requirement(r, model_specs) for r in requirements if isinstance(r, dict)]
    statuses = {r["status"] for r in results}

    if STATUS_REJECTED in statuses:
        overall = STATUS_REJECTED
    elif STATUS_NEEDS_CLARIFICATION in statuses:
        overall = STATUS_NEEDS_CLARIFICATION
    elif STATUS_UNKNOWN in statuses:
        overall = STATUS_UNKNOWN
    else:
        overall = STATUS_MATCH

    return {"status": overall, "checks": results}
