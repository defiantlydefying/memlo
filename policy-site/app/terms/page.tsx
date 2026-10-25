import type { Metadata } from "next";
import { PolicyLayout } from "../components";

export const metadata: Metadata = {
  title: "Terms of Service",
  description: "The terms governing use of the Memlo Discord bot.",
};

const sections = [
  { id: "service", label: "What Memlo provides", content: <><p>Memlo helps a class manage deadlines in administrator-selected Discord channels. Manual deadline creation, browsing, rescheduling, cancellation, and reminders work without AI. Optional AI features can detect announcements, process shared study material, answer questions, generate quizzes, and grade practice answers.</p><p>Memlo is an educational aid. It is not an official school record, learning management system, emergency-notification service, or substitute for instructions from a teacher or school. Users remain responsible for verifying deadlines, answers, and academic requirements.</p></> },
  { id: "eligibility", label: "Eligibility", content: <p>You must meet Discord’s minimum age requirement and be permitted to use Discord and Memlo under applicable law and school or organization rules. A person installing Memlo must have authority to add apps and configure the selected channels.</p> },
  { id: "acceptable-use", label: "Acceptable use", content: <><p>You may not use Memlo to break the law, Discord’s terms, or school rules; harass or exploit another person; submit highly sensitive or illegal content; evade limits or disrupt the service; infringe rights; or present generated answers as guaranteed or official.</p><p>Server administrators must select only channels they are authorized to connect and should tell members that Memlo is present and how to use its privacy controls.</p></> },
  { id: "content", label: "User content", content: <><p>You keep ownership of content you submit. You grant Memlo limited permission to receive, process, summarize, store, and display it only as needed to operate, secure, and improve the service as described in the Privacy Policy.</p><p>You represent that you have the right to submit the content. Do not upload private documents or copyrighted material you are not permitted to share.</p></> },
  { id: "ai", label: "AI-generated output", content: <p>Automated systems can misunderstand messages, miss deadlines, or produce incorrect explanations and quiz grading. Check important information against the teacher’s original message or official materials. Do not rely on Memlo for safety-critical, legal, medical, financial, or disciplinary decisions.</p> },
  { id: "privacy", label: "Privacy", content: <p>The <a href="/privacy">Memlo Privacy Policy</a> explains what data is processed, how analytics are limited, which providers are involved, and how users and administrators can delete data. It is incorporated into these Terms.</p> },
  { id: "availability", label: "Changes and availability", content: <p>Memlo is an early-stage service. Features may change, become temporarily unavailable, or be discontinued. Reasonable efforts will be made to preserve class memory and give notice of material changes, but uninterrupted operation is not guaranteed.</p> },
  { id: "termination", label: "Suspension and termination", content: <p>Access may be limited or suspended when reasonably necessary to address abuse, security, legal requirements, excessive usage, or violations of these Terms. A server administrator can stop using Memlo at any time by removing the bot.</p> },
  { id: "disclaimer", label: "Disclaimer", content: <p>To the extent permitted by law, Memlo is provided “as is” and “as available,” without warranties of accuracy, fitness for a particular purpose, non-infringement, or uninterrupted availability. Nothing in these Terms limits rights that cannot legally be waived.</p> },
  { id: "liability", label: "Limitation of liability", content: <p>To the extent permitted by law, the operator will not be liable for indirect, incidental, special, consequential, or punitive damages, or for lost data, grades, opportunities, or profits arising from use of Memlo. Where liability cannot be excluded, it is limited to the amount paid for Memlo during the twelve months before the claim. Memlo is currently free.</p> },
  { id: "updates", label: "Updates and contact", content: <p>The date at the top will change when these Terms are updated. Material changes will be announced through the support channel or server where practical. Questions: <a href="mailto:ehsieh175@gmail.com">ehsieh175@gmail.com</a>.</p> },
];

export default function TermsPage() {
  return <PolicyLayout title="Terms of Service" eyebrow="Rules for using Memlo" sections={sections} />;
}
