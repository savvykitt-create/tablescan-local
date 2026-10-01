"""Same ordered CTC recurrence as numeric_decoder, using C double arithmetic.

No fast-math, pruning, reduced beam, or altered grammar. Dict insertion order
and stable sorting deliberately preserve the reference's tie-breaking.
"""
from libc.math cimport exp, log1p, INFINITY

cdef inline double log_add(double a, double b):
    cdef double swap
    if a == -INFINITY:
        return b
    if b == -INFINITY:
        return a
    if b > a:
        swap = a
        a = b
        b = swap
    return a + log1p(exp(b - a))

cdef inline void add(dict beams, str prefix, double blank, double nonblank):
    cdef tuple old = beams.get(prefix, (-INFINITY, -INFINITY))
    beams[prefix] = (log_add(old[0], blank), log_add(old[1], nonblank))

def search(list rows, list characters, object grammar, int beam_width):
    cdef dict beams = {"": (0.0, -INFINITY)}
    cdef dict cache = grammar.prefix_cache
    cdef dict following
    cdef str prefix, char, extended
    cdef tuple state
    cdef double blank, nonblank, probability
    cdef Py_ssize_t index
    for row in rows:
        following = {}
        for prefix, state in beams.items():
            blank, nonblank = state
            add(following, prefix, log_add(blank, nonblank) + row[0], -INFINITY)
            for index in range(len(characters)):
                char = characters[index]
                probability = row[index + 1]
                if prefix.endswith(char):
                    add(following, prefix, -INFINITY, nonblank + probability)
                    extended = prefix + char
                    if extended not in cache:
                        cache[extended] = grammar.prefix_allowed(extended)
                    if cache[extended]:
                        add(following, extended, -INFINITY, blank + probability)
                else:
                    extended = prefix + char
                    if extended not in cache:
                        cache[extended] = grammar.prefix_allowed(extended)
                    if cache[extended]:
                        add(following, extended, -INFINITY, log_add(blank, nonblank) + probability)
        # Compute scores in the same order as the reference's key function.
        ranked = [(key, value, log_add(value[0], value[1])) for key, value in following.items()]
        ranked.sort(key=lambda item: item[2], reverse=True)
        beams = {key: value for key, value, score in ranked[:beam_width]}
    return beams
