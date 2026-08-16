import type { Metadata } from "next";
import { PolicyLayout } from "../components";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description: "What Memlo reads, stores, measures, and lets you delete.",
};

const sections = [
  {
    id: "summary",
    label: "The short version",
    content: <div className="callout"><ul>
      <li>Memlo reads only class channels or categories a server administrator explicitly selects. A new installation reads nothing until setup is complete.</li>
      <li>Relevant message text and study files are processed by Anthropic’s Claude API. Structured memories and study digests are stored in Memlo’s hosted database.</li>
      <li>Product analytics contain event names and operational counts only—never message text, filenames, study content, questions, or answers.</li>
      <li>Memlo does not sell personal data, show targeted ads, or train its own models on class content.</li>
      <li>Use <strong>/delete-my-data</strong> or, for administrators, <strong>/delete-server-data</strong>.</li>
    </ul></div>,
  },
  {
    id: "processing",
    label: "What Memlo processes",
    content: <>
      <h3>Selected Discord channels</h3>
      <p>Memlo receives messages, attachments, links, author information, roles, channel identifiers, and message identifiers from channels selected by a server administrator. It ignores every unselected channel. Discord requires the Message Content permission for this functionality.</p>
      <h3>Stored class memory</h3>
      <p>When content is relevant, Memlo may store a short deadline or announcement summary; the original text behind a saved note; due dates; contributor Discord ID and display name; server and channel IDs; source links; study digests; generated quizzes and answer keys; grading feedback; and any applicable class or role.</p>
      <p>Uploaded files are processed to produce a study digest. Memlo stores the digest and source reference, not a second permanent copy of the original file.</p>
      <h3>Privacy-preserving analytics</h3>
      <p>Memlo records installation, completed setup, first remembered deadline, first answer, first reminder, first completed quiz, referral-link display, and removal. Events may include server ID, timezone, scope type, source, or a numeric count.</p>
      <p>Analytics do not include message text, attachments, filenames, study digests, questions, answers, display names, or Discord user IDs.</p>
      <h3>Operational information</h3>
      <p>Memlo records timestamps and content-free error markers such as the failed component and exception type. Application logs use Discord IDs and internal record IDs where needed for diagnosis, not message content.</p>
    </>,
  },
  {
    id: "use",
    label: "How information is used",
    content: <><p>Information is used only to detect and remember class information, answer questions, deliver reminders, process study material, create and grade quizzes, enforce rate limits, keep the service reliable, and measure product activation and reliability through content-free events.</p><p>Memlo does not sell or rent data, use it for targeted advertising, build advertising profiles, or train its own machine-learning models on it.</p></>,
  },
  {
    id: "providers",
    label: "Service providers",
    content: <>
      <p><strong>Discord</strong> delivers messages and interactions to the bot. See the <a href="https://discord.com/privacy">Discord Privacy Policy</a>.</p>
      <p><strong>Anthropic</strong> processes relevant message text and study material through the Claude API. See Anthropic’s <a href="https://www.anthropic.com/legal/privacy">Privacy Policy</a> and <a href="https://www.anthropic.com/legal/commercial-terms">Commercial Terms</a>.</p>
      <p><strong>Hosting provider.</strong> Memlo runs on a hosted Linux server. Its SQLite database, provider storage where offered, operational logs, and rotating backups live on that infrastructure. The exact provider may change without changing the uses described here.</p>
      <p>No other provider receives class content unless this policy is updated first.</p>
    </>,
  },
  {
    id: "retention",
    label: "Retention and deletion",
    content: <>
      <p>Class memory remains until it is deleted, the bot is removed, or the operator removes it during maintenance. Memlo creates a database backup every six hours and retains the latest 14 snapshots. Deleted records may remain in rotating backups until the relevant snapshots expire.</p>
      <ul>
        <li><strong>/delete-my-data</strong> deletes records attributed to your Discord ID in that server after confirmation.</li>
        <li><strong>/delete-server-data</strong> lets an administrator delete all class memory, settings, and server-linked telemetry after confirmation.</li>
        <li>Removing Memlo stops collection and automatically deletes that server’s content and configuration. A content-free aggregate removal event may remain.</li>
        <li>Email <a href="mailto:ehsieh175@gmail.com">ehsieh175@gmail.com</a> with an access or deletion request.</li>
      </ul>
    </>,
  },
  {
    id: "controls",
    label: "Choices and controls",
    content: <p>Server administrators choose the channels or categories Memlo can read and may remove a scope at any time. <strong>/setup-status</strong> shows the configured timezone and tracked scopes. Removing the bot stops future processing.</p>,
  },
  {
    id: "students",
    label: "Students and younger users",
    content: <p>Memlo is intended only for people who meet Discord’s minimum age requirement, generally 13 or older and higher where local law requires it. It is not directed to children under 13. If you believe information from an underage user was processed, contact <a href="mailto:ehsieh175@gmail.com">ehsieh175@gmail.com</a>.</p>,
  },
  {
    id: "security",
    label: "Security",
    content: <p>Memlo uses explicit channel opt-in, least-privilege Discord permissions, rate limits, host filtering for fetched links, size limits, automated backups, health monitoring, and restricted server access. No internet service can guarantee perfect security, so users should not share highly sensitive personal information through Memlo.</p>,
  },
  {
    id: "changes",
    label: "Changes and contact",
    content: <p>Material changes will update the date at the top of this policy and will be announced through Memlo’s support channel or server where practical. Questions, access requests, or deletion requests: <a href="mailto:ehsieh175@gmail.com">ehsieh175@gmail.com</a>.</p>,
  },
];

export default function PrivacyPage() {
  return <PolicyLayout title="Privacy Policy" eyebrow="Clear boundaries for class data" sections={sections} />;
}
