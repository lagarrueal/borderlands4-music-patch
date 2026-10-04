"""Load a Wwise bank through wwiser's parser and index its music hierarchy.

wwiser knows every field of every HIRC type at every bank version and records
each field's byte offset, which is what in-place bank patching needs. This
module wraps it in a small API:

  b = Bank(path)
  b.obj[ulID]                      -> wwiser object node
  b.kind[ulID]                     -> 'CAkMusicSegment', 'CAkMusicTrack', ...
  b.fields(node, 'fDuration')      -> [(offset, value), ...] under node
  b.children_ids(ulID)             -> hierarchy children (music containers)
"""
import os, sys

WWISER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "wwiser.pyz")
if WWISER not in sys.path:
    sys.path.insert(0, os.path.normpath(WWISER))
from wwiser.parser import wparser  # noqa: E402


def walk(node):
    yield node
    for c in node.get_children() or ():
        yield from walk(c)


class Bank:
    def __init__(self, path):
        self.path = path
        p = wparser.Parser()
        p.parse_bank(path)
        self.root = p.get_banks()[0]
        self.obj, self.kind = {}, {}
        hirc = self.root.find1(name="listLoadedItem")    # absent in empty banks
        for item in (hirc.get_children() if hirc else None) or ():
            name = item.get_attr("name") or ""
            uid = self.field1(item, "ulID", own=True)
            if uid is not None:
                self.obj[uid[1]] = item
                self.kind[uid[1]] = name.split("[")[0]

    # -- fields --------------------------------------------------------------
    @staticmethod
    def fields(node, name, own=False):
        """Every field called `name` under node: [(offset, value)].
        own=True stops at nested objects of the same HIRC item's children lists."""
        out = []
        for n in walk(node):
            if n.get_nodename() == "field" and n.get_attr("name") == name:
                out.append((n.get_attr("offset"), n.get_attr("value")))
        return out

    def field1(self, node, name, own=False):
        for n in walk(node):
            if n.get_nodename() == "field" and n.get_attr("name") == name:
                return (n.get_attr("offset"), n.get_attr("value"))
        return None

    @staticmethod
    def objects(node, prefix):
        """Object nodes under node whose name starts with prefix."""
        return [n for n in walk(node)
                if n.get_nodename() == "object" and (n.get_attr("name") or "").startswith(prefix)]

    # -- hierarchy ------------------------------------------------------------
    def children_ids(self, uid):
        """Direct hierarchy children: the ulChildID list of a container."""
        node = self.obj[uid]
        return [v for _, v in self.fields(node, "ulChildID")]

    def parent_id(self, uid):
        f = self.field1(self.obj[uid], "DirectParentID")
        return f[1] if f else None
