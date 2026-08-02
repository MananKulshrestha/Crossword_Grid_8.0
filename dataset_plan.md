## Current Dataset Contract

The benchmark is a deterministic 100-sample e-commerce slot-filling dataset. Its
only supported attributes are `category`, `size`, `price`, `usage`, `color`, and
`brand`.

| Attribute | Priority |
| --- | --- |
| `category` | 1 |
| `size` | 2 |
| `price` | 3 |
| `usage` | 4 |
| `brand` | 5 |
| `color` | 5 |

`dataset_generator/generate.py` reads the taxonomy and templates, fills only
these slots, and writes `output/dataset_eval.json`. Every query explicitly
states its category. The generator labels omitted applicable fields as missing,
sorts them by priority, and includes at most three missing fields.

Run `python3 dataset_generator/generate.py` to recreate the same 100 samples.
