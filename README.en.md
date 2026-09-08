# Textbook Tutor and Textbook Finder

[简体中文](README.md) · **English**

Two Codex plugins that can be installed and used independently.

| Plugin | Icon | Purpose |
|---|---|---|
| **Textbook Tutor / 教材私教** (`textbook-tutor`) | <img src="plugins/textbook-tutor/assets/icon-cap-centered.png" width="72" alt="Blue graduation cap"> | Teach textbooks through Socratic dialogue in small steps, record learning evidence, and save lesson progress |
| **Textbook Finder / 教材查找** (`textbook-finder`) | <img src="plugins/textbook-finder/assets/icon-book.png" width="72" alt="Blue book"> | Find downloadable textbook PDFs from reputable sources by topic or keywords, prioritizing newer editions |

## Installation

In a version of Codex that supports plugin marketplaces, run the following commands, or give Codex this repository link and ask it to install the plugins you want:

```sh
codex plugin marketplace add https://github.com/Cyksei/tutor-plugins
codex plugin add textbook-tutor@tutor-plugins
codex plugin add textbook-finder@tutor-plugins
```

You can install either plugin on its own. After installing or updating, start a new task to load the updated skills and tools. To update an existing installation, give Codex the repository link and explicitly ask it to update the plugins to the latest repository version.

## Textbook Finder

Invoke `$textbook-finder` and describe what you want to learn; knowing a book title is optional. For example:

> I want to learn how the brain forms memories and why we forget. Find beginner-friendly textbook PDFs from reputable sources, with complete downloads and a preference for newer editions.

- Check provenance and resource ownership on publisher, university, official institution, and open textbook websites.
- Distinguish complete PDFs, chapter downloads, samples, online-only resources, and restricted access.
- Rank suitable candidates by actual publication or revision year, newest first, while respecting the topic, language, and learner’s background.
- Provide source pages and verified download entry points; download and inspect selected files when requested.
- Work independently without setting up course storage, creating lesson notes, or starting a lesson.

## Textbook Tutor

Invoke `$textbook-tutor` with a textbook, or ask to resume a saved course.

- Read the textbook, organize its overall structure and chapter key points, and teach chapter by chapter in small steps.
- Explain one concept and ask one relevant question; give feedback and advance when appropriate.
- Offer a hint after an initial mistake, then explain if more help is needed. Support direct explanations, skipping, and a fixed pace.
- Use `plan_teaching_turn` to check the next teaching action, and validate the pending question and hint stage again when saving.
- Record actual evidence per concept, distinguish independent from assisted answers, and suggest local pace adjustments and review.
- Display textbook images and add web context where useful, with sources identified.
- Keep knowledge notes separate from progress records. Curate notes during lessons, keeping familiar material concise and difficult material more detailed; store answers, outcomes, and hints in the progress file.
- Automatically create course folders with Note, Text, and Picture categories. Preserve legacy records when reorganizing an existing course.
- Use `save_checkpoint` / `resume_course` to save and restore current content, pending questions, options, and hints.

Ordinary conversation is the default control interface. You can ask to slow down, explain directly, keep a fixed pace, disable the character, review, or save progress. An optional panel tool remains available but does not open automatically.

Choose a local course-storage directory on first use and confirm it for each new course. To resume on another device, sync the entire course folder—including knowledge notes, progress records, textbooks, and images—and select its actual local location there. The plugin does not perform cloud synchronization itself.

## Course Folder Structure

After confirming the root directory, each new course is created automatically. Existing course folders are never overwritten. Knowledge updates revise the relevant entries rather than accumulating a lesson transcript.

```text
Lessons/
└── 课程：<course name>/
    ├── Note/
    │   ├── 笔记：<course name>.md
    │   └── 进度：<course name>.md
    ├── Text/
    └── Picture/
```

The Chinese prefixes mean “Course,” “Notes,” and “Progress”; they reflect the plugin’s actual folder and file naming convention. Textbooks go in Text, teaching images in the singular Picture directory, and Markdown notes link to attachments using relative paths. Progress and knowledge are saved separately. If a knowledge update fails, the saved status flags it for completion when resuming, without recording the student’s answer twice. Notes must not reveal a pending question’s solution while the student is still working through hints.

## Requirements and Limitations

Textbook Finder uses the web and download capabilities available in the current Codex environment, with no additional service dependency. Textbook Tutor includes a local stdio MCP server using Codex’s bundled Python environment. The current launcher targets macOS; Windows has not been verified. No separate Python or database installation is required.

Textbook parsing, image display/generation, and interactive controls depend on the tools and permissions available. The model interprets answers and teaches; code checks workflow and record consistency. It cannot guarantee factual correctness or that the model will invoke every tool on every turn. If a tool is unavailable, the assistant should explain the limitation and use available alternatives.

The tutor supports character roleplay. Knowledge accuracy and the learner’s choices take priority over character expression. Bilingual descriptions do not change the tutor’s default Chinese teaching language; you can request another language in conversation.

## Files and Privacy

This repository contains the two plugins’ skill instructions, tool code, tests, and active icons. It does not include textbooks, course notes, account credentials, or local directory settings. Keep private course files outside the repository.

Course-tool tests use temporary files only:

```sh
python3 -m unittest discover -s plugins/textbook-tutor/tests -v
```
