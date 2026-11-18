(* Reference oracle: OCaml PprzLink decoding/formatting without Ivy.
   stdin lines:  "D <hex>"  telemetry payload -> Ivy text (link downlink)
                 "U <ivy text>" datalink Ivy text -> payload hex (link uplink) *)
module Tm = PprzLink.Messages (struct let name = "telemetry" end)
module Dl = PprzLink.Messages (struct let name = "datalink" end)
let of_hex h = String.init (String.length h / 2) (fun i -> Char.chr (int_of_string ("0x" ^ String.sub h (2*i) 2)))
let to_hex s = String.concat "" (List.map (fun c -> Printf.sprintf "%02x" (Char.code c)) (List.init (String.length s) (String.get s)))
let () =
  try while true do
    let l = input_line stdin in
    let r = try
      match l.[0] with
      | 'D' ->
        let p = Protocol.payload_of_string (of_hex (String.sub l 2 (String.length l - 2))) in
        let (h, vs) = Tm.values_of_payload p in
        let m = Tm.message_of_id h.PprzLink.message_id in
        Printf.sprintf "%d %s" h.PprzLink.sender_id (Tm.string_of_message m vs)
      | _ ->
        let t = String.sub l 2 (String.length l - 2) in
        let (id, vs) = Dl.values_of_string t in
        let ac = PprzLink.int_assoc "ac_id" vs in
        to_hex (Protocol.string_of_payload (Dl.payload_of_values id 0 ac vs))
    with e -> "ERR"
    in print_endline r
  done with End_of_file -> ()
