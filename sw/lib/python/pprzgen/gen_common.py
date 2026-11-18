"""Port of sw/lib/ocaml/gen_common.ml: target boolean expressions."""

from .ocaml import list_sort_unique

# Boolean expressions are tuples:
#   ("any",) ("var", name) ("not", e) ("and", e1, e2) ("or", e1, e2)
ANY = ("any",)


def Var(x):
    return ("var", x)


def eval_bool(v, e):
    k = e[0]
    if k == "any":
        return True
    if k == "var":
        return v == e[1]
    if k == "not":
        return not eval_bool(v, e[1])
    if k == "and":
        return eval_bool(v, e[1]) and eval_bool(v, e[2])
    if k == "or":
        return eval_bool(v, e[1]) or eval_bool(v, e[2])
    raise AssertionError(e)


def sprint_expr(e):
    def p(s, e):
        k = e[0]
        if k == "any":
            return s + "Any "
        if k == "var":
            return f"{s} {e[1]} "
        if k == "not":
            return p(s + "Not ( ", e[1]) + ") "
        name = "And" if k == "and" else "Or"
        return p(p(s + name + " ( ", e[1]), e[2]) + ") "
    return p("", e)


def singletonize(lst, key=None):
    return list_sort_unique(lst, key)


def union(l1, l2):
    return singletonize(l1 + l2)


def _expr_of_list(items):
    if not items:
        return ANY
    if len(items) == 1:
        return Var(items[0])
    return ("or", Var(items[0]), _expr_of_list(items[1:]))


def bool_expr_of_string(s):
    """None -> Any ; "a|b" -> Or ; "!a|b" -> Not(Or ...). Str.split drops
    empty fields."""
    if s is None:
        return ANY
    if len(s) > 0 and s[0] == "!":
        return ("not", _expr_of_list([x for x in s[1:].split("|") if x != ""]))
    return _expr_of_list([x for x in s.split("|") if x != ""])


def test_targets(target, targets):
    return eval_bool(target, targets)
