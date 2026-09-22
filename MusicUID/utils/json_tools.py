"""Type-safe readers for decoded JSON from third-party endpoints.

Platform payloads are untrusted input: every field is narrowed with
``isinstance`` instead of being trusted, so no ``Any`` leaks downstream.
"""


def to_obj(value: object) -> dict[str, object]:
    """Narrow a decoded JSON value to an object with string keys.

    Args:
        value: Any decoded JSON value.

    Returns:
        The value when it is an object, otherwise an empty object.
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            out[key] = item
    return out


def to_list(value: object) -> list[object]:
    """Narrow a decoded JSON value to a list.

    Args:
        value: Any decoded JSON value.

    Returns:
        The value when it is a list, otherwise an empty list.
    """
    return value if isinstance(value, list) else []


def get_obj(obj: dict[str, object], key: str) -> dict[str, object]:
    """Read a nested object field.

    Args:
        obj: Parent object.
        key: Field name.

    Returns:
        The nested object, or an empty object when absent or mistyped.
    """
    return to_obj(obj.get(key))


def get_list(obj: dict[str, object], key: str) -> list[object]:
    """Read a nested array field.

    Args:
        obj: Parent object.
        key: Field name.

    Returns:
        The nested array, or an empty array when absent or mistyped.
    """
    return to_list(obj.get(key))


def get_str(obj: dict[str, object], key: str, default: str = "") -> str:
    """Read a string field.

    Args:
        obj: Parent object.
        key: Field name.
        default: Value returned when the field is absent or not a string.

    Returns:
        The field value or ``default``.
    """
    value = obj.get(key)
    return value if isinstance(value, str) else default


def get_bool(obj: dict[str, object], key: str, default: bool = False) -> bool:
    """Read a boolean field, accepting the 0/1 integers some APIs return.

    Args:
        obj: Parent object.
        key: Field name.
        default: Value returned when the field is absent or mistyped.

    Returns:
        The field value or ``default``.
    """
    value = obj.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return default


def get_int(obj: dict[str, object], key: str, default: int = 0) -> int:
    """Read an integer field, accepting numeric strings and floats.

    Args:
        obj: Parent object.
        key: Field name.
        default: Value returned when the field is absent or not numeric.

    Returns:
        The field value or ``default``.
    """
    value = obj.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return default


def get_id(obj: dict[str, object], key: str) -> str:
    """Read an identifier field that may be encoded as a number.

    Args:
        obj: Parent object.
        key: Field name.

    Returns:
        The identifier as a string, or an empty string when absent.
    """
    value = obj.get(key)
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    return ""


def join_names(value: object, key: str = "name") -> str:
    """Join the ``name`` fields of an array of objects (artists, singers).

    Args:
        value: The raw array value.
        key: Field name to read from each element.

    Returns:
        Names joined with ``/``, or an empty string when there is none.
    """
    names: list[str] = []
    for item in to_list(value):
        name = get_str(to_obj(item), key)
        if name:
            names.append(name)
    return "/".join(names)
