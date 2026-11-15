import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


QUANTILES = [f"{i / 10:.1f}" for i in range(1, 10)]


class InputError(ValueError):
    pass


class ForecastData:
    def __init__(self, folder):
        folder = Path(folder)
        with (folder / "routes.csv").open(encoding="utf-8-sig", newline="") as f:
            self.routes = {r["route"]: r for r in csv.DictReader(f)}
        paths = list(folder.glob("*my_forecast.csv"))
        if len(paths) != 1:
            raise ValueError("В папке data нужен ровно один файл *my_forecast.csv")
        self.rows = {}
        self.dates = set()
        with paths[0].open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            required = {"route", "date", "seats"} | {
                f"{q}_{field}" for q in QUANTILES
                for field in ("base_demand", "price", "price_demand")
            }
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("Не хватает колонок прогноза")
            for row in reader:
                route, day = row["route"], row["date"]
                key = (route, day)
                if key in self.rows or route not in self.routes:
                    raise ValueError("Дубликат рейса или неизвестный маршрут")
                date.fromisoformat(day)
                seats = int(row["seats"])
                values = [Decimal(row[f"{q}_base_demand"]) for q in QUANTILES]
                if seats <= 0 or any(not d.is_finite() or d < 0 for d in values):
                    raise ValueError("Недопустимый спрос или вместимость")
                if values != sorted(values):
                    raise ValueError("Квантили спроса должны идти по возрастанию")
                self.rows[key] = {"seats": seats, "demand": values}
                self.dates.add(day)
        if not self.rows:
            raise ValueError("Файл прогноза пуст")

    def catalog(self):
        routes = []
        for key, info in self.routes.items():
            if not any(r == key for r, _ in self.rows):
                continue
            routes.append({
                "route": key, "name": info["name"],
                "destination": info["name"].split("—")[-1].strip(),
                "base_price": float(info["base_price"]),
                "price_min": float(info["price_min"]),
                "price_max": float(info["price_max"]),
                "dates": sorted(day for route, day in self.rows if route == key),
            })
        return {"routes": routes, "start_date": min(self.dates),
                "end_date": max(self.dates), "flights": len(self.rows)}

    def calculate(self, route, day, price):
        if not isinstance(route, str) or route not in self.routes:
            raise InputError("Выберите направление из списка.")
        if not isinstance(day, str) or (route, day) not in self.rows:
            raise InputError("На выбранную дату прогноза нет. Выберите дату из доступного периода.")
        if isinstance(price, (bool, list, dict)) or price is None:
            raise InputError("Введите цену билета в рублях.")
        try:
            price = Decimal(str(price))
        except (InvalidOperation, ValueError):
            raise InputError("Введите цену билета числом.") from None
        if not price.is_finite() or price < 0 or price > 1_000_000:
            raise InputError("Цена должна быть от 0 до 1 000 000 ₽.")
        if price != price.quantize(Decimal("0.01")):
            raise InputError("Укажите цену с точностью до копеек.")
        info, row = self.routes[route], self.rows[(route, day)]
        p0, sensitivity = Decimal(info["base_price"]), Decimal(info["price_sensitivity"])
        scenarios = [d - sensitivity * (price - p0) for d in row["demand"]]
        demand = scenarios[4]
        sold = min(Decimal(row["seats"]), max(Decimal(0), demand))
        return {
            "route": route, "destination": info["name"].split("—")[-1].strip(),
            "date": day, "price": float(price), "base_price": float(p0),
            "seats": row["seats"], "demand": float(demand), "sold": float(sold),
            "free_seats": float(Decimal(row["seats"]) - sold),
            "scenarios": [{"quantile": float(q), "demand": float(d)}
                          for q, d in zip(QUANTILES, scenarios)],
        }
