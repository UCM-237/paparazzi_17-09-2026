(* print parsed tree in an unambiguous form; errors -> "ERROR" *)
let rec dump b = function
  | Xml.PCData s -> Buffer.add_string b (Printf.sprintf "T%d:%s" (String.length s) s)
  | Xml.Element (t, a, c) ->
    Buffer.add_string b (Printf.sprintf "E%d:%s[" (String.length t) t);
    List.iter (fun (n, v) -> Buffer.add_string b (Printf.sprintf "A%d:%s=%d:%s" (String.length n) n (String.length v) v)) a;
    Buffer.add_string b "](";
    List.iter (dump b) c;
    Buffer.add_string b ")"
let () =
  let noprove = Array.length Sys.argv > 1 && Sys.argv.(1) = "-noprove" in
  for i = (if noprove then 2 else 1) to Array.length Sys.argv - 1 do
    let f = Sys.argv.(i) in
    let r = try
      let x = if noprove then (let p = XmlParser.make () in XmlParser.prove p false; XmlParser.parse p (XmlParser.SFile f)) else Xml.parse_file f in
      let b = Buffer.create 1000 in dump b x;
      Digest.to_hex (Digest.string (Buffer.contents b)) ^ " " ^ Digest.to_hex (Digest.string (Xml.to_string_fmt x)) ^ " " ^ Digest.to_hex (Digest.string (Xml.to_string x))
    with _ -> "ERROR" in
    print_endline (f ^ " " ^ r)
  done
