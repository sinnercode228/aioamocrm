# Contributing to aioamocrm

Bug reports, fixes, tests and corrections to the READMEs are welcome, and so is an answer
recorded from a real amoCRM or Kommo account, since the tests have none. This file says how to
set up the project, which checks a change has to pass, and how changes are made here. A short
Russian version is at the end.

Questions, bugs and proposals go to
[GitHub issues](https://github.com/sinnercode228/aioamocrm/issues). A security problem goes
through [SECURITY.md](SECURITY.md) instead, not a public issue. Everyone taking part follows the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Setting up

Python 3.10 or newer:

```sh
git clone https://github.com/sinnercode228/aioamocrm
cd aioamocrm
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

This is what CI installs. `dev` brings pytest with pytest-asyncio and pytest-cov, respx, ruff,
mypy, FastAPI for the webhook example and its tests, build and twine.

## Running the tests

```sh
pytest
pytest --cov     # the same, with branch coverage and the lines it missed
```

No test talks to amoCRM. The client's requests go through httpx to
[respx](https://lundberg.github.io/respx/) mocks, so the suite needs no account and no token.

CI also runs the suite on Python 3.10 with the lowest supported versions. FastAPI needs a newer
pydantic than 2.5.0, so it is removed there, and the tests that need it are skipped. To do the
same, in a Python 3.10 environment set up as above:

```sh
pip install "httpx==0.25.0" "pydantic==2.5.0"
pip uninstall -y fastapi
pytest
```

pytest is configured strictly in `pyproject.toml`: a warning fails the test that raised it, an
unknown marker is an error, and a test marked `xfail` that passes fails.

## Lint, format and types

```sh
ruff check
ruff format --check     # plain "ruff format" rewrites the files
mypy --strict src
mypy                    # src, tests and examples, strict as set in pyproject.toml
```

CI runs these four commands and fails on any finding. The ruff rule set is in `pyproject.toml`;
among others it wants Google-style docstrings on public objects, annotations everywhere, no
`print` outside the examples and no naive datetimes. `Any` is allowed for JSON bodies, and
Cyrillic letters in strings and comments are intended. Lines are at most 100 characters.

## How a change is made

- **Test first.** Every change in behaviour comes with a test that failed before it, and a bug
  fix starts with the test that shows the bug. Run the new test against `main` and see it fail,
  then fix the code. The test and the fix go in the same commit.
- **Test tools.** `tests/conftest.py` has three fixtures: `mock`, a respx router for
  `https://example.amocrm.ru`; `sleeps`, which makes retry backoff instant and records every
  delay the client asked for; and `amo`, an open client of that account with a long-lived token
  and no rate limit. `tests/payloads.py` has answers shaped like the examples in the amoCRM
  documentation. Check the requests the client sent, not only what it returned.
- **Where a shape comes from.** Request and answer shapes follow the amoCRM documentation and
  the official PHP SDK, as the README's "What was checked and how" lists. A change to a shape
  says which of them it follows, with a link to the SDK code where it applies. An answer
  recorded from a real account is welcome, with the tokens and personal data removed and a note
  on where it came from.
- **Code.** The package passes `mypy --strict`, public objects have docstrings, logging goes
  through `logging.getLogger(__name__)`, and nothing logs a token, the client secret, a query
  string or a body. No error carries a token.
- **Writes.** A `POST` or `PATCH` that may have reached amoCRM (answered with 429 or 5xx, or
  timed out after it was sent) is not sent again unless the call passed `retry=True`, because
  the amoCRM API has no idempotency keys. A change to retries keeps that; the table under
  "What is retried" in the README says what is retried for which method.

## The READMEs

README.md is in English and README.ru.md in Russian; a change to one goes to the other in the
same pull request. README.md is also the project description on PyPI, so links to files in the
repository are absolute GitHub URLs, as in the rest of the README.

The README samples are tests, in `tests/test_readme.py`:

- Every `python` or `pycon` block follows a `<!-- sample: name -->` marker. A block without one
  fails the test; code that is not meant to run goes in a `text` or `sh` block.
- Both READMEs carry the same set of sample names, with comments and texts translated.
- Each sample has a test named after it (`test_token_storage` for `token-storage`) that mocks
  the endpoints it calls and checks the requests it sent. `pycon` blocks run as doctests, and
  code with top-level `await` runs inside an open client named `amo` of `example.amocrm.ru`.
- The first five lines of each README link the other.

A new sample needs its test in the same commit. The sdist ships both READMEs and the tests, so
this test also runs from an unpacked sdist.

## Changelog

[CHANGELOG.md](CHANGELOG.md) follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Add the entry under the next version's heading, `## [<version>] - Unreleased` (create it if it
is not there), in `Added`, `Changed` or `Fixed`, and write it for someone who uses the library.
A change that breaks existing code says so at the start of its entry. Releases are made by the
maintainer, as the README's Releasing section describes.

## Commits and pull requests

- A commit message is one line in the imperative that says what changes, in plain words, with
  no body, no prefix such as `feat:` and no full stop, for example
  `Never follow redirects, also with a client passed as http=` or
  `Take a 202 whose body is not JSON as a success, as the official SDK does`.
- One change per commit. A fix and its test go together; an unrelated cleanup goes in its own
  commit.
- Pull requests go to `main`. CI has to pass: lint and types, the tests on Python 3.10 to 3.13
  and on Python 3.10 with the lowest versions, and building the sdist and the wheel with
  `twine check --strict`.

## По-русски

Окружение, команды и правила те же, что выше; коротко они описаны в разделе
[«Участие в разработке»](https://github.com/sinnercode228/aioamocrm/blob/main/README.ru.md#участие-в-разработке)
README.ru.md. Любое изменение поведения приходит с тестом, который до него падал. Перед
pull request прогоните `ruff check`, `ruff format --check`, `mypy --strict src`, `mypy` и
`pytest`. Каждый пример на Python в README стоит после метки `<!-- sample: имя -->`, есть в
обоих README и проверяется своим тестом в `tests/test_readme.py`. Запись в CHANGELOG.md добавьте
под заголовок следующей версии. Вопросы и баги пишите в issues, об уязвимостях сообщайте так,
как написано в SECURITY.md.
