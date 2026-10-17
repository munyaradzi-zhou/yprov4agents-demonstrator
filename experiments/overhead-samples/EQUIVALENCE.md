Time, against OFF: median paired difference and 95% interval; equivalent means the 90% interval lies inside +/-1 ms (TOST, 5%).

| Agent | Build (B-TRANS) | Equivalent | Build and write (B) | Equivalent |
|---|--:|:-:|--:|:-:|
| CA3 Analytics (heuristic) | +76 us [+34, +124] | yes | +1,278 us [+1,164, +1,391] | no |
| CA2 Metrics | -193 us [-1,522, +1,358] | no | +746 us [+73, +1,276] | no |
| CAx Summariser (computed) | +81 us [+38, +161] | yes | +1,158 us [+1,084, +1,230] | no |
| MC1 Compliance | +53 us [+53, +54] | yes | +964 us [+959, +986] | yes |
| MC3 Audit | +56 us [+55, +56] | yes | +997 us [+982, +1,025] | no |
| CA1 Storage (persist path) | +48 us [-20, +106] | yes | +1,178 us [+1,057, +1,260] | no |

CPU energy per session, against OFF (gross, paired by round):

| Condition | Difference | 95% interval | Upper bound |
|---|--:|--:|--:|
| A | +0.110 J (+8%) | [+0.005, +0.217] J | +15% |
| B-TRANS | +0.039 J (+3%) | [-0.068, +0.134] J | +9% |
| B | +0.222 J (+16%) | [+0.134, +0.318] J | +22% |
