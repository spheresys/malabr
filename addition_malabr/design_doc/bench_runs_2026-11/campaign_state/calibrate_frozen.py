"""Fresh calibration on the frozen build. Keyed by machine and build in the file
path, and by model, mode, KV type, n_ctx, n_seq_max and threads inside the file."""
import json, os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
import campaign as K
mode, n_ctx = sys.argv[1], int(sys.argv[2])
m = K.Malabr(mode, n_ctx)
fb = m.cal.get("fallback")
print(mode, n_ctx, "fallback" if fb else "measured", json.dumps(m.cal.get("key")),
      "decode", m.cal.get("decode_curve"), "prefill", m.cal.get("prefill_curve"), flush=True)
m.shutdown()
sys.exit(1 if fb else 0)
