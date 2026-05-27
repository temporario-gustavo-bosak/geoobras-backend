"""
services/geometry_service.py
Converte WKT (Well-Known Text) em lat/long e mantém o WKT para o campo geom.
Usa Shapely para parse sem precisar de PostGIS no Python.

NOTA: Se PostGIS estiver ativo no banco, as funções ST_* do Postgres podem
substituir esse serviço. Por enquanto trabalhamos só com WKT + lat/lon numérico.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Brazil bounding box — coordinates outside this range are rejected as bogus
_BRAZIL_LAT_MIN: float = -35.0
_BRAZIL_LAT_MAX: float = 6.0
_BRAZIL_LON_MIN: float = -75.0
_BRAZIL_LON_MAX: float = -30.0


def _valid_brazil(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return _BRAZIL_LAT_MIN <= lat <= _BRAZIL_LAT_MAX and _BRAZIL_LON_MIN <= lon <= _BRAZIL_LON_MAX


def _try_shapely(wkt: str) -> tuple[float | None, float | None]:
    """Tenta WKT primeiro; se falhar, tenta WKB hex (fallback para APIs que retornam geometrias em hex)."""
    try:
        from shapely import wkt as shapely_wkt

        geom = shapely_wkt.loads(wkt)
        centroid = geom.centroid
        return centroid.y, centroid.x  # lat = y, lon = x
    except Exception as exc:
        logger.debug("Shapely WKT falhou: %s — tentando WKB hex", exc)

    try:
        from shapely import wkb as shapely_wkb

        geom = shapely_wkb.loads(wkt, hex=True)
        centroid = geom.centroid
        return centroid.y, centroid.x
    except Exception as exc:
        logger.debug("Shapely WKB hex também falhou: %s", exc)
        return None, None


def extract_lat_lon(geometria_wkt: Optional[str]) -> tuple[float | None, float | None]:
    """
    Extrai (latitude, longitude) de um WKT.
    Suporta POINT, POLYGON, MULTIPOLYGON etc. via centroide.
    Valida que as coordenadas estão dentro do Brasil.

    Retorna (None, None) se não conseguir parsear ou se as coordenadas forem inválidas.
    """
    if not geometria_wkt:
        return None, None

    wkt = geometria_wkt.strip()

    # Atalho rápido para POINT simples: "POINT (-41.78 -22.37)"
    if wkt.upper().startswith("POINT"):
        try:
            coords = wkt.split("(")[1].rstrip(")").strip().split()
            lon, lat = float(coords[0]), float(coords[1])
            if _valid_brazil(lat, lon):
                return lat, lon
            logger.debug("Coordenadas POINT fora do Brasil: lat=%s lon=%s", lat, lon)
            return None, None
        except (IndexError, ValueError):
            pass

    lat, lon = _try_shapely(wkt)
    if not _valid_brazil(lat, lon):
        if lat is not None or lon is not None:
            logger.debug("Coordenadas Shapely fora do Brasil: lat=%s lon=%s", lat, lon)
        return None, None
    return lat, lon


def wkt_to_geom_text(geometria_wkt: Optional[str]) -> Optional[str]:
    """
    Retorna o WKT normalizado (strip) para armazenar no campo geom (TEXT).
    Se PostGIS estiver disponível no futuro, este campo pode ser convertido
    para geometry com ST_GeomFromText(geom, 4326).
    """
    if not geometria_wkt:
        return None
    return geometria_wkt.strip()
