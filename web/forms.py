from __future__ import annotations

from dataclasses import dataclass

from flask import Request


@dataclass
class ProductForm:
    name: str
    description: str
    price: float
    category_id: int | None
    photo_id: str | None

    @classmethod
    def from_request(cls, request: Request) -> 'ProductForm':
        category_raw = (request.form.get('category_id') or '').strip()
        category_id = int(category_raw) if category_raw.isdigit() else None
        return cls(
            name=(request.form.get('name') or '').strip(),
            description=(request.form.get('description') or '').strip(),
            price=float((request.form.get('price') or '0').replace(',', '.')),
            category_id=category_id,
            photo_id=(request.form.get('photo_id') or '').strip() or None,
        )


@dataclass
class CategoryForm:
    name: str
    description: str

    @classmethod
    def from_request(cls, request: Request) -> 'CategoryForm':
        return cls(
            name=(request.form.get('name') or '').strip(),
            description=(request.form.get('description') or '').strip(),
        )


@dataclass
class DeliveryZoneForm:
    zone_name: str
    cost: float
    description: str

    @classmethod
    def from_request(cls, request: Request) -> 'DeliveryZoneForm':
        return cls(
            zone_name=(request.form.get('zone_name') or '').strip(),
            cost=float((request.form.get('cost') or '0').replace(',', '.')),
            description=(request.form.get('description') or '').strip(),
        )
