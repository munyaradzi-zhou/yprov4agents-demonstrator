| Slice | A: callback baseline | B: explicit capture |
|---|--:|--:|
| all | 227/426 (53%, 49 to 58) | 426/426 (100%, 99 to 100) |
| gpt-4o-2024-08-06 | 121/213 (57%, 50 to 63) | 213/213 (100%, 98 to 100) |
| gpt-4o-mini-2024-07-18 | 106/213 (50%, 43 to 56) | 213/213 (100%, 98 to 100) |
| ca1 | 68/210 (32%, 26 to 39) | 210/210 (100%, 98 to 100) |
| ca2 | 6/54 (11%, 5 to 22) | 54/54 (100%, 93 to 100) |
| ca3 | 45/54 (83%, 71 to 91) | 54/54 (100%, 93 to 100) |
| cax | 108/108 (100%, 97 to 100) | 108/108 (100%, 97 to 100) |
| cause: consumption-unconfirmed | 7/54 (13%, 6 to 24) | 54/54 (100%, 93 to 100) |
| cause: fallback | 0/48 (0%, 0 to 7) | 48/48 (100%, 93 to 100) |
| cause: incomplete | 54/54 (100%, 93 to 100) | 54/54 (100%, 93 to 100) |
| cause: incomplete-input | 0/54 (0%, 0 to 7) | 54/54 (100%, 93 to 100) |
| cause: integrity | 13/54 (24%, 15 to 37) | 54/54 (100%, 93 to 100) |
| cause: none | 108/108 (100%, 97 to 100) | 108/108 (100%, 97 to 100) |
| cause: stall | 39/48 (81%, 68 to 90) | 48/48 (100%, 93 to 100) |
| cause: ungrounded | 6/6 (100%, 61 to 100) | 6/6 (100%, 61 to 100) |

Paired: B right where A wrong 199, A right where B wrong 0, exact McNemar p = 2.5e-60. Confident (>= 0.8) and wrong: A 199, B 0.
