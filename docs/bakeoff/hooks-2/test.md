# Bake-off 2 summary: 60 moments

Win rate of each arm against the production hook (win 1, split 0.5, loss 0; both orders; an arm that writes nothing loses).

| Arm | Judge | all | en | ar |
|---|---|---|---|---|
| spec | j1 | 37% (17 won, 8 split, 32 lost of 57) | 42% (14 won, 6 split, 20 lost of 40) | 24% (3 won, 2 split, 12 lost of 17) |
| spec | j2 | 67% (34 won, 8 split, 15 lost of 57) | 66% (24 won, 5 split, 11 lost of 40) | 68% (10 won, 3 split, 4 lost of 17) |
| angle | j1 | 41% (15 won, 17 split, 25 lost of 57) | 48% (13 won, 12 split, 15 lost of 40) | 26% (2 won, 5 split, 10 lost of 17) |
| angle | j2 | 40% (16 won, 14 split, 27 lost of 57) | 39% (10 won, 11 split, 19 lost of 40) | 44% (6 won, 3 split, 8 lost of 17) |

No hook written (failures): {'old': 1, 'spec': 1, 'angle': 3}
Errors: 3