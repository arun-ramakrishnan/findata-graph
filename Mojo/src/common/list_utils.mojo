"""
Shared string-sort routines (consolidation: colocation, NOT dedup).

`sort_strs` (insertion) and `merge_sort_strs` (merge) are different
algorithms by design — insertion for the <= ~1.5k key lists, merge for
the big link-predict lists — sharing one module is the win. Canonical
home for integrity_check's `sort_strs` and graph_algos_probe's
`_merge_sorted` + `merge_sort_strs` pair.

Flat import (Makefile.mojo passes -I per package dir):
  from list_utils import sort_strs, merge_sort_strs
"""


def sort_strs(lst: List[String]) -> List[String]:
    """Insertion sort (lists here are <= ~1.5k entries)."""
    var out = lst.copy()
    for i in range(1, len(out)):
        var key = out[i]
        var j = i - 1
        while j >= 0 and out[j] > key:
            out[j + 1] = out[j]
            j -= 1
        out[j + 1] = key
    return out^


def _merge_sorted(a: List[String], b: List[String]) -> List[String]:
    var out = List[String]()
    var i = 0
    var j = 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            out.append(a[i])
            i += 1
        else:
            out.append(b[j])
            j += 1
    while i < len(a):
        out.append(a[i])
        i += 1
    while j < len(b):
        out.append(b[j])
        j += 1
    return out^


def merge_sort_strs(lst: List[String]) -> List[String]:
    """O(n log n) sort for the big lists (link-predict ~20k pairs);
    sort_strs' insertion sort is for the <= ~1.5k key lists."""
    if len(lst) <= 1:
        return lst.copy()
    var mid = len(lst) // 2
    var left = List[String]()
    var right = List[String]()
    for i in range(len(lst)):
        if i < mid:
            left.append(lst[i])
        else:
            right.append(lst[i])
    return _merge_sorted(merge_sort_strs(left), merge_sort_strs(right))


def main():
    # Smoke: every src/<pkg>/*.mojo file must carry a main() or
    # `mojo build` refuses it ("module does not contain a 'main'
    # function") — the Makefile builds ALL of src/ into Mojo/bin/.
    var lst = List[String]()
    lst.append("c")
    lst.append("a")
    lst.append("b")
    print("list_utils smoke:", sort_strs(lst), merge_sort_strs(lst))
