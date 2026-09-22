# Amanat ID: Flutter client and account backend design

## Goal

Create a polished Amanat ID interface matching the supplied references while keeping the existing face-recognition and attendance logic. The new client is a separate Flutter application that runs on Web, Android, and iOS. FastAPI remains the backend and becomes the single source of authentication and attendance data.

## Scope

The first release contains three complete experiences:

1. A combined sign-in and registration screen for parents and administrators.
2. An administrator dashboard with school summary, recent events, weekly attendance, navigation to students, classes, reports, and camera controls.
3. A parent dashboard that shows exactly one linked child, recent arrival/departure records, and attendance percentage.

Teacher workflows, face recognition, existing student management, and report generation remain supported by the backend. The Flutter shell may expose these existing staff functions after the three primary screens are complete, but no unrelated school-management modules are added.

## Repository layout

The Flutter app will live in a new top-level `flutter/` directory. Existing Python code stays in `app/`, `frontend/`, and compatibility entry points. Flutter will use feature-oriented folders for authentication, administrator, parent, shared widgets, networking, models, and theme.

## Responsive interface

The visual system follows the references: navy typography, bright blue actions, pale blue-gray backgrounds, white rounded cards, subtle borders, and restrained shadows.

- Desktop/Web uses a persistent left sidebar, top application bar, multi-column metric cards, and wide dashboard panels.
- Tablet collapses dashboard grids while retaining the sidebar where practical.
- Phone uses a compact top bar or navigation drawer, single-column cards, touch-friendly controls, and scrollable content.
- The sign-in screen remains centered and readable at every width.
- The UI uses Flutter-native widgets and responsive breakpoints; React and embedded HTML are not used.

## Authentication and registration

The backend gains a dedicated account table with unique login, password hash, role, optional linked student, creation timestamp, and active status. Passwords are stored with the project’s existing PBKDF2 hashing approach and never returned by the API.

Parent registration requires:

- parent login;
- password and password confirmation;
- the parent/child invitation code already assigned to a student;
- identification of the matching parent and student when needed to disambiguate a reused code.

The backend validates the invitation code and links the account to exactly one student. The parent API always derives the child from the authenticated account; the client cannot request another child by changing a name or code.

Administrator registration requires a login, password, and the configured `ADMIN_TOKEN` invitation code. The token is validated server-side and is never stored in Flutter source code or returned by the API.

Successful sign-in returns an opaque bearer session token. Sessions are stored server-side with role, account identifier, creation time, and expiration. Flutter stores the token using secure platform storage on Android/iOS. On Web, the client uses a browser-appropriate protected persistence mechanism and avoids placing credentials or invitation codes connected to the URL.

Legacy environment-password administrator access can remain as a compatibility path during migration, but all new Flutter sign-ins use account records.

## API boundaries

FastAPI adds JSON endpoints for:

- parent and administrator registration;
- sign-in, sign-out, and current-session identity;
- administrator summary metrics;
- recent attendance events and weekly attendance aggregation;
- the authenticated parent’s linked child and daily attendance history.

Existing APIs are reused where their authorization and response shape are appropriate. New protected endpoints enforce role checks on the server, not only in Flutter navigation.

Validation errors use a consistent JSON shape containing a stable error code and a user-readable Russian message. Duplicate logins, invalid invitation codes, already-linked students, weak passwords, expired sessions, and forbidden role access have separate responses.

## Flutter application structure

The application has these bounded modules:

- `core`: API configuration, HTTP client, token persistence, routing, theme, and responsive breakpoints.
- `auth`: sign-in/register tabs, role selector, form validation, and authentication state.
- `admin`: dashboard metrics, recent events, weekly chart, sidebar, and shortcuts.
- `parent`: linked-child hero card, recent-day attendance, profile navigation, and quick actions.
- `shared`: logo treatment, buttons, cards, badges, empty/loading/error states, and adaptive page scaffold.

Authentication state decides the initial route. Parent and administrator route groups reject the wrong role and return to sign-in after session expiry.

## Data flow and states

Each screen follows one flow: Flutter controller requests a typed API model, displays a skeleton/loading state, then renders data or an actionable error. Empty attendance and empty school data are normal states with clear Russian copy rather than blank cards.

Registration validates fields locally for immediate feedback and repeats all security-sensitive validation on the backend. A successful registration signs the user in and opens the dashboard for that role.

## Security and compatibility

- Logins are normalized consistently and unique regardless of surrounding whitespace.
- Passwords have a minimum length and are compared only through password-hash verification.
- Invitation codes are compared securely and are never included in logs or API responses.
- Parent authorization is account-to-student based on every request.
- API CORS configuration explicitly supports the deployed Flutter Web origin in production.
- Existing SQLite installations are upgraded idempotently without deleting students or attendance.
- Existing camera and recognition services remain unchanged except where authenticated API access is required.

## Testing and acceptance

Backend tests cover database migration, successful and rejected registrations, duplicate login, invalid invitation code, sign-in/sign-out, role authorization, and prevention of access to another child.

Flutter tests cover form validation, authentication state, responsive layouts at desktop/tablet/phone widths, role-based routing, loading/empty/error states, and rendering of administrator and parent API data.

The result is accepted when:

- the same Flutter source builds for Web, Android, and iOS;
- a parent can register by code, sign in, and see only the linked child;
- an administrator can register with `ADMIN_TOKEN`, sign in, and see live database metrics;
- layouts visually follow all three references and remain usable at phone and desktop sizes;
- existing student, attendance, and recognition data are preserved.
