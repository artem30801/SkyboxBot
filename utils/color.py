#!/usr/bin/env python3

"""
Gives the name of any RGB color.

If the exact color doesn't have a name, the closest match will be used instead.
"""


import functools

from utils.color_dicts import colors, searchtree


@functools.singledispatch
def find_color_name(r, g, b):
    """Finds a color's name.

    The color may be expressed in either of the following formats:
     - three ints (r, g, b) in the range 0 <= x < 256,
     - a tuple of three ints (r, g, b) in the range 0 <= x < 256, or
     - a hexadecimal representation (3 or 6 digits, '#' prefix optional).
    """
    if type(r) is not int or type(g) is not int or type(b) is not int:
        raise TypeError("R, G and B values must be int")
    if not (0 <= r < 256 and 0 <= g < 256 and 0 <= b < 256):
        raise ValueError("Invalid color value: must be 0 <= x < 256")
    return _search(searchtree, r, g, b)


@find_color_name.register(str)
def _find_hex(color):
    color = hex2rgb(color)
    return find_color_name(*color)

@find_color_name.register(tuple)
def _find_tuple(color):
    if len(color) != 3:
        raise ValueError("Malformed color tuple: must be of size 3 (r, g, b)")
    return find_color_name(*color)

def _octree_index(r, g, b, d):
    return ((r >> d & 1) << 2) | ((g >> d & 1) << 1) | (b >> d & 1)

def _search(tree, r, g, b, d=7):
    i = _octree_index(r, g, b, d)
    if i not in tree:
        return _approximate(tree, r, g, b)
    return tree[i] if type(tree[i]) is str else _search(tree[i], r, g, b, d-1)

def _approximate(tree, r, g, b):
    def _distance(colorname):
        x, y, z = colors[colorname]
        return (r - x)**2 + (g - y)**2 + (b - z)**2
    return min(_descendants(tree), key=_distance)

def _descendants(tree):
    for i, child in tree.items():
        if type(child) is str:
            yield child
        else:
            yield from _descendants(child)


def clamp(x):
    return max(0, min(x, 255))


def rgb2hex(r, g, b):
    return "#{0:02x}{1:02x}{2:02x}".format(clamp(r), clamp(g), clamp(b))


def hex2rgb(color: str):
    if color[0] == '#':
        color = color[1:]

    if len(color) == 3:
        r, g, b = [int(c * 2, 16) for c in color]
    elif len(color) == 6:
        r, g, b = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
    else:
        raise ValueError("Malformed hexadecimal color representation")

    return r, g, b


color_names = {rgb2hex(*value): name for name, value in colors.items()}

if __name__ == "__main__":
    exact = [
        ("Amaranth", (229,  43,  80)),
        ("Bamboo"  , (218,  99,   4)),
        ("Camelot" , (137,  52,  86)),
        ("Denim"   , ( 21,  96, 189)),
        ("Elephant", ( 18,  52,  71))]
    approximate = [
        ("Black"   , ( 1,   3,   2)),
        ("White"   , (254, 255, 255))]
    print("Exact matches:")
    for name, color in exact:
        result = find_color_name(color)
        print("  {:16} Expected: {:9} Actual: {}".format(str(color), name, result))
    print("Approximate matches:")
    for name, color in approximate:
        result = find_color_name(color)
        print("  {:16} Expected: {:9} Actual: {}".format(str(color), name, result))

