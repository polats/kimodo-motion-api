# Sims-style animation library

A working list of motions a life-sim game needs, with the Mixamo search
query that pulls a usable take. The bulk-import script
(`web/scripts/bulk_import_mixamo_animations.py`) reads this list and
imports the top-30 baseline; everything beyond is opt-in.

Conventions:
- **`q`** is the Mixamo search query. The first result is taken.
- Tagged categories let the UI filter by need: `idle`, `locomotion`,
  `social`, `daily`, `work`, `play`, `combat`, `sleep`, `accident`.

| # | Slug                       | Category    | Mixamo query                     |
|---|----------------------------|-------------|----------------------------------|
| 1 | idle                       | idle        | Idle                             |
| 2 | idle_breathing             | idle        | Breathing Idle                   |
| 3 | idle_happy                 | idle        | Happy Idle                       |
| 4 | walk                       | locomotion  | Walking                          |
| 5 | walk_slow                  | locomotion  | Slow Walk                        |
| 6 | walk_strut                 | locomotion  | Strut Walking                    |
| 7 | walk_in_place              | locomotion  | Walking In Place                 |
| 8 | run                        | locomotion  | Running                          |
| 9 | run_in_place               | locomotion  | Running In Place                 |
| 10| jump                       | locomotion  | Jumping                          |
| 11| jump_in_place              | locomotion  | Jumping In Place                 |
| 12| sit_idle                   | daily       | Sitting Idle                     |
| 13| sit_to_stand               | daily       | Stand To Sit                     |
| 14| stand_to_sit               | daily       | Sitting Down                     |
| 15| sit_chair_idle             | daily       | Sit Chair Idle                   |
| 16| sleep_idle                 | sleep       | Sleeping Idle                    |
| 17| lying_idle                 | sleep       | Lying Down                       |
| 18| eating                    | daily        | Eating                           |
| 19| drinking                  | daily        | Drinking                         |
| 20| wave_hello                 | social      | Waving                           |
| 21| wave_goodbye               | social      | Waving Gesture                   |
| 22| clap                       | social      | Clapping                         |
| 23| laugh                      | social      | Laughing                         |
| 24| cheer                      | social      | Cheering                         |
| 25| cry                        | social      | Crying                           |
| 26| angry                     | social      | Angry Gesture                    |
| 27| shrug                      | social      | Shrugging                        |
| 28| nod                        | social      | Nodding                          |
| 29| shake_head                 | social      | Shaking Head No                  |
| 30| talking                    | social      | Talking                          |
| 31| hug                        | social      | Hug                              |
| 32| kiss                       | social      | Kiss                             |
| 33| dance                     | play         | Dancing                          |
| 34| dance_hip_hop              | play        | Hip Hop Dancing                  |
| 35| dance_salsa                | play        | Salsa Dancing                    |
| 36| dance_silly                | play        | Silly Dancing                    |
| 37| read_book                  | work        | Reading Book                     |
| 38| typing                     | work        | Typing                           |
| 39| cooking                    | work        | Cooking                          |
| 40| sweeping                   | work        | Sweeping                         |
| 41| pickup                     | daily       | Picking Up Object                |
| 42| pushing                    | daily       | Pushing                          |
| 43| pulling                    | daily       | Pulling                          |
| 44| throw                     | combat       | Throwing                         |
| 45| punch                     | combat       | Punching                         |
| 46| kick                      | combat       | Kicking                          |
| 47| fall                      | accident     | Falling Back                     |
| 48| trip                      | accident     | Tripping                         |
| 49| stumble                   | accident     | Stumble Backwards                |
| 50| death                     | accident     | Death                            |

## Notes

- Mixamo "in-place" variants are usually preferable for game integration
  (you control root motion separately). The non-in-place variants are
  useful for cutscenes or single-shot demos.
- Some queries return multiple matches; we always take the first.
  Curate the list by running search interactively first if the wrong
  take comes back.
- All Mixamo animations are auto-rigged to the `mixamorig:*` skeleton,
  so any imported Mixamo character can play any imported Mixamo
  animation directly via three.js' AnimationMixer (no retargeting).
