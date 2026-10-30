"""Route composition only; doors, widths and exit independence need separate checks."""
from __future__ import annotations

BASE_SEQUENCES = frozenset(tuple(s.split('-')) for s in (
    'R-E', 'R-C-E', 'R-V-E', 'R-C-V-E', 'R-V-C-E',
    'R-R-E', 'R-R-C-E', 'R-R-V-E', 'R-R-C-V-E',
))


def normalize_sequence(zones: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for zone in zones:
        if zone == 'T' or (zone == 'C' and result and result[-1] == 'C'):
            continue
        result.append(zone)
    return tuple(result)


def admissible_sequence(zones: list[str], *, same_floor: bool = True,
                        terminal_is_outside: bool = False,
                        neighbor_is_f5_ab: bool | None = False,
                        technical_without_permanent_workplaces: bool = False,
                        equipment_services_that_hazardous_room: bool = False) -> bool:
    """Apply RO-19 without turning every vestibule into an ordinary room.

    A caller must establish the real zone functions, doors and terminal.
    Unknown category cannot establish the neighboring-room exception.
    """
    seq = normalize_sequence(zones)
    two_vestibules = seq == ('R', 'V', 'C', 'V', 'E')
    if seq not in BASE_SEQUENCES and not two_vestibules:
        return False
    neighboring = two_vestibules or seq[:2] == ('R', 'R')
    if neighboring:
        if not same_floor or neighbor_is_f5_ab is None:
            return False
        if neighbor_is_f5_ab and not (technical_without_permanent_workplaces
                                      and equipment_services_that_hazardous_room):
            return False
    return not two_vestibules or terminal_is_outside
