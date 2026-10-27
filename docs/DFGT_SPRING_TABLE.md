Synthetic vectors; MockBackend only; no measured torque. Global gain 100%, polarity -1, slew 327670.
Spring gain 1 -> 2; coefficient 32767; saturation 65535; deadband 0.

| Position | PID Spring | EVDEV before | Calibrated Spring | EVDEV after | Command range after | Final clipping |
|---:|---:|---:|---:|---:|---:|:---:|
| -100% | 32767.00 | -32767 | 65534.00 | -32767 | 100.0% | yes |
| -50% | 16383.50 | -16384 | 32767.00 | -32767 | 100.0% | no |
| -25% | 8191.75 | -8192 | 16383.50 | -16384 | 50.0% | no |
| -10% | 3276.70 | -3277 | 6553.40 | -6553 | 20.0% | no |
| +0% | 0.00 | 0 | 0.00 | 0 | 0.0% | no |
| +10% | -3276.70 | 3277 | -6553.40 | 6553 | 20.0% | no |
| +25% | -8191.75 | 8192 | -16383.50 | 16384 | 50.0% | no |
| +50% | -16383.50 | 16384 | -32767.00 | 32767 | 100.0% | no |
| +100% | -32767.00 | 32767 | -65534.00 | 32767 | 100.0% | yes |
