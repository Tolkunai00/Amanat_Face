# Flutter Amanat Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a responsive Flutter client for Web, Android, and iOS with real parent/admin registration and authenticated dashboards backed by the existing FastAPI attendance system.

**Architecture:** Add account/session persistence and role-protected JSON endpoints to the existing SQLite/FastAPI backend. Create a separate feature-oriented Flutter application in `flutter/`; it communicates only through typed API services and renders adaptive desktop/mobile shells matching the supplied references.

**Tech Stack:** Python 3, FastAPI, SQLite, PBKDF2-HMAC-SHA256, pytest, Flutter/Dart, Material 3, `http`, `flutter_secure_storage`, `fl_chart`, `flutter_test`.

## Global Constraints

- One Flutter source tree must build for Web, Android, and iOS.
- A parent account is linked to exactly one student and can never select another child through request parameters.
- Administrator registration requires the configured `ADMIN_TOKEN`; parent registration requires the invitation code stored on the student.
- Existing students, attendance, face embeddings, and recognition behavior must remain intact.
- UI copy is Russian and the responsive appearance follows the three supplied references.
- Passwords use the existing PBKDF2 helper and invitation codes must never appear in responses or logs.

---

## File map

- `app/database/database.py`: idempotent account/session schema and account queries.
- `app/admin/admin_panel.py`: authentication, authorization, and dashboard API routes.
- `requirements.txt`: backend test dependency.
- `tests/conftest.py`: isolated SQLite configuration and FastAPI test client fixtures.
- `tests/test_accounts.py`: account registration, login, and authorization tests.
- `tests/test_dashboard_api.py`: admin and parent dashboard aggregation tests.
- `flutter/pubspec.yaml`: Flutter dependencies and assets.
- `flutter/lib/main.dart`: app bootstrap.
- `flutter/lib/core/`: theme, responsive breakpoints, API client, session storage, router/auth state.
- `flutter/lib/features/auth/`: combined sign-in/registration UI.
- `flutter/lib/features/admin/`: administrator dashboard and adaptive navigation.
- `flutter/lib/features/parent/`: linked-child dashboard.
- `flutter/lib/shared/`: reusable logo, cards, badges, and state widgets.
- `flutter/test/`: widget and controller tests.

### Task 1: Persist accounts and sessions safely

**Files:**
- Modify: `app/database/database.py`
- Create: `tests/conftest.py`
- Create: `tests/test_accounts.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `create_account(login: str, password: str, role: str, student_name: str | None) -> bool`
- Produces: `authenticate_account(login: str, password: str) -> tuple[int, str, str, str | None] | None`
- Produces: `get_student_for_invitation(code: str, parent_name: str, student_name: str) -> tuple | None`
- Produces: `student_has_parent_account(student_name: str) -> bool`
- Produces: `create_session(account_id: int, ttl_seconds: int = 43200) -> str`, `get_session(token: str) -> tuple | None`, `delete_session(token: str) -> None`

- [ ] **Step 1: Add isolated database fixtures and failing persistence tests**

```python
def test_parent_account_and_session_round_trip(isolated_db):
    assert db.create_account("parent1", "password1", "parent", "Айлин")
    account = db.authenticate_account("parent1", "password1")
    assert account[2:] == ("parent", "Айлин")
    token = db.create_session(account[0], ttl_seconds=60)
    assert db.get_session(token)[1:] == ("parent1", "parent", "Айлин")
    db.delete_session(token)
    assert db.get_session(token) is None

def test_login_is_unique_after_normalization(isolated_db):
    assert db.create_account(" Parent1 ", "password1", "parent", "Айлин")
    assert not db.create_account("parent1", "password2", "parent", "Айлин")
```

- [ ] **Step 2: Run `pytest tests/test_accounts.py -q` and verify failures report missing account functions**

- [ ] **Step 3: Extend `init_db()` with `accounts` and `auth_sessions` tables**

```sql
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  login TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin', 'parent')),
  student_name TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(student_name) REFERENCES students(name) ON UPDATE CASCADE ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_parent_account_per_student
ON accounts(student_name) WHERE role = 'parent' AND student_name IS NOT NULL;
CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY,
  account_id INTEGER NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
);
```

- [ ] **Step 4: Implement normalized account queries and SHA-256-hashed opaque session tokens using parameterized SQL**

- [ ] **Step 5: Run `pytest tests/test_accounts.py -q` and confirm all persistence tests pass**

- [ ] **Step 6: Commit with `git commit -am "feat: add account and session persistence"` after staging new test files**

### Task 2: Expose registration and session APIs

**Files:**
- Modify: `app/admin/admin_panel.py`
- Modify: `tests/test_accounts.py`

**Interfaces:**
- Consumes: account/session functions from Task 1.
- Produces: `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`.
- Produces: `current_account(request: Request, required_role: str | None = None) -> dict`.

- [ ] **Step 1: Add failing API tests for parent/admin success, duplicate login, invalid code, weak password, second account for one child, logout, and wrong-role access**

```python
def test_parent_registration_links_exact_student(client, seeded_student):
    response = client.post("/api/auth/register", json={
        "role": "parent", "login": "aigul", "password": "strongpass",
        "password_confirmation": "strongpass", "invitation_code": "P-20481",
        "parent_name": "Айгуль Касымова", "student_name": "Айлин Касымова"
    })
    assert response.status_code == 201
    assert response.json()["user"] == {
        "login": "aigul", "role": "parent", "student_name": "Айлин Касымова"
    }
    assert "token" in response.json()
```

- [ ] **Step 2: Run the targeted tests and verify the new routes return 404**

- [ ] **Step 3: Add Pydantic request models, a shared error response helper, bearer-token extraction, and role authorization**

```json
{"error":{"code":"invalid_invitation","message":"Неверный код регистрации."}}
```

- [ ] **Step 4: Implement registration so parent codes resolve one named student and admin codes use constant-time comparison with `ADMIN_TOKEN`**

- [ ] **Step 5: Implement login/logout/me with 12-hour opaque sessions and never return password hashes or invitation codes**

- [ ] **Step 6: Run `pytest tests/test_accounts.py -q` and confirm every auth case passes**

- [ ] **Step 7: Commit `app/admin/admin_panel.py` and tests as `feat: add account registration api`**

### Task 3: Add protected dashboard APIs

**Files:**
- Modify: `app/database/database.py`
- Modify: `app/admin/admin_panel.py`
- Create: `tests/test_dashboard_api.py`

**Interfaces:**
- Consumes: `current_account()` from Task 2.
- Produces: `GET /api/dashboard/admin` and `GET /api/dashboard/parent`.

- [ ] **Step 1: Add failing tests that seed attendance and assert live admin counts, five-day chart values, recent events, parent daily rows, percentage, and cross-role 403 responses**

```python
def test_parent_dashboard_ignores_requested_student(client, parent_headers, seeded_attendance):
    response = client.get("/api/dashboard/parent?student_name=Другой", headers=parent_headers)
    assert response.status_code == 200
    assert response.json()["student"]["name"] == "Айлин Касымова"
```

- [ ] **Step 2: Run `pytest tests/test_dashboard_api.py -q` and verify route-not-found failures**

- [ ] **Step 3: Add focused database aggregation functions for today’s distinct arrivals/departures, latest status, recent events, five school days, and one student’s daily attendance**

- [ ] **Step 4: Implement role-protected endpoints with stable typed JSON fields consumed by Flutter**

- [ ] **Step 5: Run `pytest tests/test_dashboard_api.py -q tests/test_accounts.py -q` and confirm both suites pass**

- [ ] **Step 6: Commit as `feat: add role based dashboard api`**

### Task 4: Scaffold the cross-platform Flutter client and core services

**Files:**
- Create: `flutter/` via `flutter create --platforms=web,android,ios --org kg.amanat flutter`
- Modify: `flutter/pubspec.yaml`
- Create: `flutter/lib/core/config/app_config.dart`
- Create: `flutter/lib/core/api/api_client.dart`
- Create: `flutter/lib/core/auth/auth_controller.dart`
- Create: `flutter/lib/core/auth/session_store.dart`
- Create: `flutter/lib/core/theme/app_theme.dart`
- Create: `flutter/lib/core/responsive/breakpoints.dart`
- Create: `flutter/test/core/auth_controller_test.dart`

**Interfaces:**
- Produces: `ApiClient`, `SessionStore`, `AuthController`, `AuthUser`, `AppTheme`, and `AppBreakpoints`.

- [ ] **Step 1: Generate the Flutter project and add `http`, `flutter_secure_storage`, `fl_chart`, and `google_fonts` dependencies**

- [ ] **Step 2: Write failing controller tests using a fake API client for session restore, login success, invalid credentials, and logout**

```dart
test('login stores token and exposes role', () async {
  final store = MemorySessionStore();
  final controller = AuthController(api: FakeApiClient.success(), store: store);
  await controller.login('admin1', 'strongpass');
  expect(controller.user?.role, UserRole.admin);
  expect(await store.readToken(), 'test-token');
});
```

- [ ] **Step 3: Run `flutter test test/core/auth_controller_test.dart` and verify failures for missing classes**

- [ ] **Step 4: Implement API base URL from `--dart-define=API_BASE_URL=...`, JSON error mapping, secure token persistence, and observable auth state**

- [ ] **Step 5: Implement Material 3 theme with navy `#07153A`, blue `#1769FF`, background `#F3F7FF`, 20–28px radii, Inter typography, and desktop/tablet/phone breakpoints at 1180/760px**

- [ ] **Step 6: Run `dart format .`, `flutter analyze`, and the controller test; confirm no diagnostics and passing tests**

- [ ] **Step 7: Commit as `feat: scaffold Flutter client core`**

### Task 5: Build the real sign-in and registration screen

**Files:**
- Create: `flutter/lib/features/auth/auth_page.dart`
- Create: `flutter/lib/features/auth/auth_form_controller.dart`
- Create: `flutter/lib/shared/amanat_logo.dart`
- Create: `flutter/lib/shared/segmented_selector.dart`
- Create: `flutter/test/features/auth/auth_page_test.dart`

**Interfaces:**
- Consumes: `AuthController` and `ApiClient` from Task 4.
- Produces: `AuthPage` and validated login/register submissions.

- [ ] **Step 1: Write widget tests for login/register tabs, role switching, role-specific invitation fields, password confirmation, busy state, Russian validation messages, and phone/desktop overflow**

- [ ] **Step 2: Run the widget test and confirm it fails because `AuthPage` is absent**

- [ ] **Step 3: Implement the centered white card, Amanat ID logo treatment, tab selector, parent/admin selector, labeled fields, blue submit button, and inline API errors from the first reference**

- [ ] **Step 4: Connect successful login/registration to `AuthController` and clear secret fields after failures**

- [ ] **Step 5: Run `flutter test test/features/auth/auth_page_test.dart`, `flutter analyze`, and `dart format lib test`**

- [ ] **Step 6: Commit as `feat: build Flutter authentication screen`**

### Task 6: Build the administrator dashboard

**Files:**
- Create: `flutter/lib/features/admin/admin_dashboard_page.dart`
- Create: `flutter/lib/features/admin/admin_dashboard_controller.dart`
- Create: `flutter/lib/features/admin/widgets/admin_sidebar.dart`
- Create: `flutter/lib/features/admin/widgets/metric_card.dart`
- Create: `flutter/lib/features/admin/widgets/recent_events_card.dart`
- Create: `flutter/lib/features/admin/widgets/weekly_chart_card.dart`
- Create: `flutter/test/features/admin/admin_dashboard_test.dart`

**Interfaces:**
- Consumes: `GET /api/dashboard/admin` through `ApiClient`.
- Produces: responsive `AdminDashboardPage` matching the second reference.

- [ ] **Step 1: Write tests for the four metrics, recent status badges, five-day chart, loading/error/empty states, desktop sidebar, and mobile drawer**

- [ ] **Step 2: Run the tests and verify missing-widget failures**

- [ ] **Step 3: Implement typed dashboard models/controller and stale-request protection on refresh**

- [ ] **Step 4: Implement the desktop two-region layout, top role control, four metric cards, recent events list, weekly bar chart, camera action, and adaptive mobile order**

- [ ] **Step 5: Connect sidebar actions to available backend pages/API features; disable unavailable destinations with clear labels instead of dead taps**

- [ ] **Step 6: Run admin widget tests at 1440×900, 900×900, and 390×844; run `flutter analyze`**

- [ ] **Step 7: Commit as `feat: add responsive admin dashboard`**

### Task 7: Build the one-child parent dashboard and app routing

**Files:**
- Create: `flutter/lib/features/parent/parent_dashboard_page.dart`
- Create: `flutter/lib/features/parent/parent_dashboard_controller.dart`
- Create: `flutter/lib/features/parent/widgets/child_summary_card.dart`
- Create: `flutter/lib/features/parent/widgets/recent_days_card.dart`
- Create: `flutter/lib/core/router/app_router.dart`
- Modify: `flutter/lib/main.dart`
- Create: `flutter/test/features/parent/parent_dashboard_test.dart`
- Create: `flutter/test/app_routing_test.dart`

**Interfaces:**
- Consumes: `GET /api/dashboard/parent` and authentication state.
- Produces: role-aware app routing and responsive `ParentDashboardPage` matching the third reference.

- [ ] **Step 1: Write tests for child identity, latest status, arrival/departure values, attendance percentage, recent-day badges, empty state, role redirects, and logout**

- [ ] **Step 2: Run tests and verify missing page/router failures**

- [ ] **Step 3: Implement typed parent dashboard model/controller; do not expose a student-name request argument**

- [ ] **Step 4: Implement the blue gradient child card, recent days, quick actions, parent sidebar/drawer, and adaptive single-column phone view**

- [ ] **Step 5: Implement root routing: unauthenticated → auth, admin → admin dashboard, parent → parent dashboard, expired/invalid session → auth**

- [ ] **Step 6: Run all Flutter tests, `flutter analyze`, and `dart format --output=none --set-exit-if-changed lib test`**

- [ ] **Step 7: Commit as `feat: add parent dashboard and routing`**

### Task 8: End-to-end verification and operator documentation

**Files:**
- Modify: `README.md`
- Create: `flutter/README.md`

**Interfaces:**
- Consumes: complete backend and Flutter app.
- Produces: reproducible local run/build instructions.

- [ ] **Step 1: Document backend start, `ADMIN_TOKEN`, development CORS, Flutter Web launch, Android emulator address (`10.0.2.2`), physical-device LAN address, and production `--dart-define` examples**

- [ ] **Step 2: Run `pytest -q` and record that all backend tests pass**

- [ ] **Step 3: Run `flutter test` and `flutter analyze` from `flutter/` and record clean output**

- [ ] **Step 4: Run `flutter build web`; run `flutter build apk --debug`; on macOS/CI run `flutter build ios --no-codesign` because iOS compilation is unavailable on Windows**

- [ ] **Step 5: Start FastAPI and Flutter Web, then manually verify parent registration → one-child dashboard and admin registration → live admin dashboard at desktop and phone browser widths**

- [ ] **Step 6: Run `git diff --check` and inspect `git status --short` so generated build artifacts and secrets are not staged**

- [ ] **Step 7: Commit documentation and final integration changes as `docs: add Flutter run and build guide`**
