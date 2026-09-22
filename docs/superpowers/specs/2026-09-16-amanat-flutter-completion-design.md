# Amanat ID Flutter Completion Design

## Goal

Finish the Amanat ID Flutter application as a production-quality adaptive client for administrators, teachers, and parents. The visual language follows `C:\Users\user\Downloads\amanat_faceid_ui_v2.html`, while the existing FastAPI backend and SQLite database remain the source of truth for authentication, students, attendance, camera recognition, and reports.

## Product decisions

- The Flutter application is the maintained client; the HTML file is a visual and interaction reference only.
- The product supports three roles: administrator, teacher, and parent.
- There is no public registration. Administrators create teacher and parent accounts and issue initial credentials.
- Existing student, attendance, face embedding, photo, and account data must be preserved.
- All user-visible copy is Russian.
- Demonstration data and fake-success buttons from the HTML reference are not carried into the finished application.
- The same Flutter source must remain usable on Web, Android, and iOS.

## Visual system and responsiveness

The application reproduces the reference's visual character with Flutter-native widgets: navy typography, saturated blue primary actions, pale blue-gray page backgrounds, white surfaces, subtle borders, restrained shadows, rounded cards, compact status pills, and clear spacing hierarchy.

The theme defines reusable colors, typography, radii, elevations, form styles, buttons, tables, badges, dialogs, loading states, and error states. Light and dark themes are fully implemented, and the selected theme is persisted locally.

Desktop and wide web layouts use a fixed left navigation rail, a sticky-style top area, multi-column dashboard cards, and wide tables. Tablet layouts collapse dashboard grids progressively. Phone layouts use a compact app bar, bottom navigation for primary destinations, single-column content, touch targets of at least 44 logical pixels, and scroll-safe forms and dialogs. No supported viewport may overflow horizontally except intentionally scrollable data tables.

## Role experiences

### Administrator

The administrator shell contains Home, Cameras, Students, Classes, Reports, Users, and Settings.

- Home shows live school metrics, recent attendance, a weekly chart, and working shortcuts.
- Cameras show arrival and departure feeds, explicit start/stop controls, connection/loading/error states, and recent recognition results.
- Students support real search, class filtering, viewing, creating, editing, and deleting.
- Classes aggregate actual students and attendance; selecting a class opens a filtered report.
- Reports support class/date/status filters and download a real UTF-8 CSV file compatible with Excel.
- Users allow administrators to create teachers and parent accounts, assign teacher classes, inspect account status, and reset passwords.
- Settings include persisted theme selection, notification preference UI backed by local settings, session information, and logout.

### Teacher

The teacher shell uses the same visual system but exposes only assigned classes and permitted data. It contains Home, My Classes, Attendance, Reports, and Profile.

- Home summarizes assigned classes and current attendance.
- My Classes opens only classes assigned by an administrator.
- Attendance and reports show real records for those classes.
- Teacher actions never expose administrator-only account, student deletion, or camera-management privileges.
- Profile shows the signed-in identity, assigned classes, theme controls, and logout.

### Parent

The parent shell contains Home, History, Statistics, and Profile.

- Home presents the linked child's identity, current presence, today's arrival/departure, attendance percentage, and recent days.
- History presents chronological real attendance records with clear status badges and empty states.
- Statistics summarizes attendance from backend data without allowing the parent to select another child.
- Profile shows the account identity, theme controls, password-support instructions, and logout.

## Student photo workflow

The Add Student and Edit Student experiences use a dedicated, reusable form rather than an unstructured alert dialog. The form contains student full name, class, parent full name, parent login, an initial parent password when creating the account, and an image field.

The image field supports choosing an image available to the platform, immediate preview, replacement, and removal before submission. A photo is mandatory when creating a student and optional when editing an existing student. The UI validates required fields, login length, password length, supported image selection, and photo presence before sending. The backend remains authoritative and validates upload size, decodability, exactly one detected face, duplicate students, and duplicate parent logins. Failed creation removes any partially saved photo and does not create a partial student or parent account.

Student list rows display the stored photo when available and initials as a fallback. Selecting a row opens the real student details/edit flow. Deletion requires an explicit confirmation that names the student and is available only to administrators.

## Architecture and component boundaries

The current feature-oriented Flutter structure is retained and refined instead of replaced wholesale.

- `core/api` owns HTTP, multipart upload, typed error mapping, and report download requests.
- `core/auth` owns session restoration, login, logout, and authenticated identity.
- `core/theme` owns light/dark themes and theme persistence.
- `core/responsive` owns viewport breakpoints and adaptive shell decisions.
- `features/admin` owns administrator pages, student management, camera controls, account management, and reports.
- `features/teacher` owns assigned-class dashboards and reports.
- `features/parent` owns linked-child dashboards, history, and statistics.
- `shared` owns adaptive scaffold, navigation, buttons, status badges, state views, photo input, confirmation dialogs, and CSV download helpers that are genuinely shared.

Large existing widgets are split only where necessary to isolate behavior and make it testable. Controllers expose immutable view data and explicit loading/error state. Widgets do not contain backend authorization rules; the backend enforces every role boundary.

## Backend and API changes

Existing authentication and dashboard endpoints remain the foundation. The backend is extended only where the completed UI requires a real operation.

Required protected capabilities are:

- list, create, inspect, update, and delete students;
- multipart student creation and update with photo validation;
- list and create managed teacher/parent accounts, assign teacher classes, and reset passwords;
- administrator, teacher, and parent dashboard payloads;
- class and attendance report queries with server-side role filtering;
- recent recognition/attendance events;
- a report response that Flutter can convert to a deterministic CSV download.

API errors retain a stable JSON envelope with a machine-readable code and a Russian user message. Authentication uses opaque bearer sessions. Parent identity is always resolved from the authenticated account. Teacher class access and administrator-only mutations are checked on the server for every request.

## Interaction and data flow

Each page follows one predictable flow: begin loading, request typed data, render content or an actionable error, and allow an explicit retry. Mutations disable duplicate submission, show progress, preserve entered form values on failure, display the backend's safe Russian message, and refresh the affected data after success.

Navigation actions always lead to a real destination. Buttons are removed when the corresponding operation is outside the role's permissions. Empty data is rendered as a designed state with an explanation and, where permitted, a relevant action. Camera errors distinguish permission, connection, decoding, and server errors.

CSV export uses the currently selected class/date/status filters, stable columns, UTF-8 with BOM for Excel compatibility, escaped values, and a descriptive filename. On Web it downloads through the browser; supported native platforms use the platform save/share mechanism already available through Flutter-compatible abstractions.

## Error handling and safety

- Network timeouts, offline state, expired sessions, validation errors, and server failures use distinct messages.
- A 401 response clears the local session and returns to sign-in.
- A 403 response keeps the session but presents a permission error and hides the prohibited action.
- Destructive operations require confirmation and refresh data only after server success.
- Student creation and photo persistence are atomic from the user's perspective.
- Existing database migrations remain idempotent and never delete production rows.
- Secrets, passwords, parent codes, bearer tokens, and face embeddings are never logged or rendered.

## Testing strategy

Development follows test-driven cycles. Backend tests use a temporary SQLite database and controlled face-recognition doubles. Flutter controller and widget tests use real state transitions with narrow fake API boundaries.

Automated coverage includes:

- login, session restoration, logout, role routing, and authorization failures;
- administrator creation of teachers and parents and password reset;
- student creation, update, deletion, photo-required validation, upload failures, zero-face and multiple-face rejection, and cleanup after conflict;
- administrator, teacher, and parent dashboards derived from real seeded data;
- teacher class restrictions and parent-to-child isolation;
- navigation, search, filters, class-to-report flow, CSV contents, theme persistence, confirmations, loading, empty, success, and error states;
- desktop, tablet, and phone widget sizes without unintended overflow.

Final verification requires the complete backend test suite, `flutter test`, `flutter analyze`, a Flutter Web production build, and an interactive Chrome pass against the running FastAPI server. The browser pass covers primary actions for all three roles at desktop and phone viewport sizes and checks browser console errors.

## Acceptance criteria

The work is accepted when:

1. Administrator, teacher, and parent can sign in with administrator-issued credentials and reach only authorized screens.
2. Every visible action performs a real operation, navigates to a real screen, or is intentionally unavailable with a clear reason; no demo toast substitutes for unfinished behavior.
3. An administrator can create a student with a previewed photo and parent account, then view, edit, filter, report on, and delete that student safely.
4. Camera controls and attendance displays use backend data and expose useful loading, offline, and error states.
5. CSV export reflects active filters and opens correctly in Excel.
6. Light/dark themes and adaptive navigation match the approved HTML visual direction on desktop and phone.
7. Existing school data remains intact, automated checks pass, the web build succeeds, and the main workflows pass interactive browser verification.

