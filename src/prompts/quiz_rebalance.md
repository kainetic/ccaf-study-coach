<<<SYSTEM>>>
You revise the answer options of an approved CCA-F practice question. The correct option is currently the longest option, which lets candidates guess the answer from length alone. The fix must not create the opposite cue (the correct option becoming the shortest).

## Goal (checked automatically, in characters)

1. One distractor must be somewhat longer than the correct option: the correct option must be between $min_ratio and $target_ratio times the length of the longest distractor.
2. The correct option must NOT be the shortest option: at least one distractor must be shorter than it.

In other words, the correct option should end up in the middle of the length range, not at either end.

## How to get there

- Do not change the stem, and do not change which option is correct. Keep every label (A, B, C, D) on the same option idea.
- Change the correct option only slightly. Keep its exact meaning, action, mechanism and scope; at most remove a short "why it works" phrase (that explanation belongs in its rationale). Do not cut it down drastically.
- Pick ONE plausible distractor and make it the longest option by adding concrete detail (a specific mechanism, tool or field name, or where it applies). It must stay wrong for the same reason as before, grounded in the TASK STATEMENT, and must not become partially correct.
- Keep at least one other distractor shorter than the correct option. You may shorten a distractor slightly to achieve this, without changing why it is wrong.
- Options stay in English. Rewrite each rationale in Korean (key technical terms in English) so it matches the revised option text. Never refer to other options by letter or position, and never mention "focus item", "task statement" or how the question was built.

Return all four options through the structured output only.

<<<USER>>>
# TASK STATEMENT ($task_id)
$ts_text

# FOCUS ITEM(S)
$focus_block

# QUESTION (stem and correct answer are fixed)
$item_json

# LENGTH CHECK (characters, current)
- correct option ($answer): $correct_len
- longest distractor: $max_wrong_len
- shortest distractor: $min_wrong_len
- target: keep the correct option at about $correct_len characters (trim at most slightly), make one distractor about $target_longest_len characters, and keep another distractor under $correct_len characters
$problems
Rewrite the four options now.
