import hashlib, sys
sys.path.insert(0, sys.argv[1])
from pprzgen import xml_light as X
def dump(x, out):
    if isinstance(x, str):
        out.append(f"T{len(x.encode('latin-1'))}:{x}")
    else:
        out.append(f"E{len(x.tag)}:{x.tag}[")
        for n, v in x.attribs:
            out.append(f"A{len(n)}:{n}={len(v.encode('latin-1'))}:{v}")
        out.append("](")
        for c in x.children: dump(c, out)
        out.append(")")
md5 = lambda s: hashlib.md5(s.encode('latin-1')).hexdigest()
args = sys.argv[2:]
noprove = args and args[0] == "-noprove"
if noprove: args = args[1:]
for f in args:
    try:
        if noprove:
            p = X.XmlParser(); p.prove = False; x = p.parse_file(f)
        else:
            x = X.parse_file(f)
        o = []; dump(x, o)
        r = md5("".join(o)) + " " + md5(X.to_string_fmt(x)) + " " + md5(X.to_string(x))
    except Exception as e:
        r = "ERROR"
    print(f, r)
