# -*- coding: utf-8 -*-
"""Persistent alert rule storage and evaluation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
import tempfile
from typing import Any


ALLOWED_FIELDS = {"TotalScore", "Mom12M", "RSI", "ATRPercent"}
ALLOWED_OPS = {">", ">=", "<", "<=", "==", "!="}


@dataclass
class AlertRule:
    id: int
    name: str
    field: str
    op: str
    threshold: float
    ticker: str | None = None
    enabled: bool = True


class AlertRuleStore:
    def __init__(self, path: str = "alert_rules.json"):
        self.path = path
        self._rules: list[AlertRule] = []
        self._next_id = 1
        self._load()

    def add(
        self,
        name: str,
        field: str,
        op: str,
        threshold: float,
        *,
        ticker: str | None = None,
    ) -> AlertRule:
        validated_field = self._validate_field(field)
        validated_op = self._validate_op(op)
        validated_threshold = self._coerce_threshold(threshold)
        validated_ticker = self._normalize_ticker(ticker)

        rule = AlertRule(
            id=self._next_id,
            name=str(name),
            field=validated_field,
            op=validated_op,
            threshold=validated_threshold,
            ticker=validated_ticker,
            enabled=True,
        )
        self._rules.append(rule)
        self._next_id += 1
        self._save()
        return rule

    def remove(self, rule_id: int) -> bool:
        for index, rule in enumerate(self._rules):
            if rule.id == rule_id:
                del self._rules[index]
                self._save()
                return True
        return False

    def update(self, rule_id: int, **kwargs) -> bool:
        rule = self._find_rule(rule_id)
        if rule is None:
            return False

        updates: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key == "id":
                raise ValueError("id cannot be updated")
            if key == "name":
                updates[key] = str(value)
            elif key == "field":
                updates[key] = self._validate_field(value)
            elif key == "op":
                updates[key] = self._validate_op(value)
            elif key == "threshold":
                updates[key] = self._coerce_threshold(value)
            elif key == "ticker":
                updates[key] = self._normalize_ticker(value)
            elif key == "enabled":
                updates[key] = bool(value)
            else:
                raise ValueError(f"unknown field: {key}")

        for key, value in updates.items():
            setattr(rule, key, value)
        self._save()
        return True

    def list(self) -> list[AlertRule]:
        return [AlertRule(**asdict(rule)) for rule in self._rules]

    def evaluate(self, data: dict) -> list[AlertRule]:
        matches: list[AlertRule] = []
        for rule in self._rules:
            if self._matches(rule, data):
                matches.append(AlertRule(**asdict(rule)))
        return matches

    def evaluate_batch(self, results: list[dict]) -> list[tuple[dict, AlertRule]]:
        matches: list[tuple[dict, AlertRule]] = []
        for row in results:
            for rule in self.evaluate(row):
                matches.append((row, rule))
        return matches

    def _find_rule(self, rule_id: int) -> AlertRule | None:
        for rule in self._rules:
            if rule.id == rule_id:
                return rule
        return None

    def _matches(self, rule: AlertRule, data: dict) -> bool:
        if not rule.enabled:
            return False

        if rule.ticker is not None and data.get("Ticker") != rule.ticker:
            return False

        value = data.get(rule.field)
        if value is None:
            return False

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return False

        return self._compare(numeric_value, rule.op, rule.threshold)

    @staticmethod
    def _compare(left: float, op: str, right: float) -> bool:
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        raise ValueError(f"invalid operator: {op}")

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self._rules = []
            self._next_id = 1
            return

        with open(self.path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)

        if not isinstance(payload, list):
            raise ValueError("alert rules json must contain a list")

        rules: list[AlertRule] = []
        max_id = 0
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("each alert rule must be an object")
            rule = self._rule_from_dict(item)
            rules.append(rule)
            max_id = max(max_id, rule.id)

        self._rules = rules
        self._next_id = max_id + 1

    def _save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix="alert_rules_",
            suffix=".tmp",
            dir=directory,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(
                    [asdict(rule) for rule in self._rules],
                    fh,
                    ensure_ascii=False,
                    indent=2,
                )
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

    def _rule_from_dict(self, item: dict[str, Any]) -> AlertRule:
        try:
            rule_id = int(item["id"])
            name = str(item["name"])
            field = self._validate_field(item["field"])
            op = self._validate_op(item["op"])
            threshold = self._coerce_threshold(item["threshold"])
            ticker = self._normalize_ticker(item.get("ticker"))
            enabled = bool(item.get("enabled", True))
        except KeyError as exc:
            raise ValueError(f"missing rule key: {exc.args[0]}") from exc

        return AlertRule(
            id=rule_id,
            name=name,
            field=field,
            op=op,
            threshold=threshold,
            ticker=ticker,
            enabled=enabled,
        )

    @staticmethod
    def _validate_field(field: Any) -> str:
        if field not in ALLOWED_FIELDS:
            raise ValueError(f"invalid field: {field}")
        return str(field)

    @staticmethod
    def _validate_op(op: Any) -> str:
        if op not in ALLOWED_OPS:
            raise ValueError(f"invalid operator: {op}")
        return str(op)

    @staticmethod
    def _coerce_threshold(value: Any) -> float:
        try:
            threshold = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid threshold: {value}") from exc
        if not math.isfinite(threshold):
            raise ValueError(f"invalid threshold: {value}")
        return threshold

    @staticmethod
    def _normalize_ticker(ticker: Any) -> str | None:
        if ticker is None:
            return None
        return str(ticker)


if __name__ == "__main__":
    with tempfile.NamedTemporaryFile(
        prefix="alert_rules_test_",
        suffix=".json",
        delete=False,
    ) as fh:
        path = fh.name

    try:
        os.remove(path)
        store = AlertRuleStore(path)
        total_rule = store.add("High TotalScore", "TotalScore", ">=", 80)
        rsi_rule = store.add("Low RSI", "RSI", "<", 30)
        ticker_rule = store.add("AAPL Momentum", "Mom12M", ">", 10, ticker="AAPL")

        assert len(store.list()) == 3

        matched = store.evaluate({"Ticker": "AAPL", "TotalScore": 85})
        assert [rule.id for rule in matched] == [total_rule.id]

        batch = store.evaluate_batch(
            [
                {"Ticker": "AAPL", "TotalScore": 85, "Mom12M": 12},
                {"Ticker": "MSFT", "RSI": 25},
                {"Ticker": "NVDA", "ATRPercent": None},
            ]
        )
        assert len(batch) == 3
        assert batch[0][0]["Ticker"] == "AAPL" and batch[0][1].id == total_rule.id
        assert batch[1][0]["Ticker"] == "AAPL" and batch[1][1].id == ticker_rule.id
        assert batch[2][0]["Ticker"] == "MSFT" and batch[2][1].id == rsi_rule.id

        assert store.update(total_rule.id, threshold=90)
        assert store.evaluate({"Ticker": "AAPL", "TotalScore": 85}) == []
        assert store.evaluate({"Ticker": "AAPL", "TotalScore": 95})[0].id == total_rule.id

        assert store.remove(rsi_rule.id)
        assert len(store.list()) == 2
        print("ALERT_RULES OK")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
