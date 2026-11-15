# Compliance checklist

Run this checklist before any work in a regulated area.
Regulated areas are: health data, children, financial or legal advice, payments for others, and personal data at scale.
This is not legal advice. It tells the fleet when to slow down and what to write down.

## How to run it

1. Name the area. Write it in the project's `STATE.md` under "Compliance".
2. Answer every question for that area. Write each answer and the date.
3. If one answer is "no" or "unknown" for a required item, stop work in that area. Pivot or ask the owner in the brief.
4. Save the result as `projects/<slug>/COMPLIANCE.md`.

## Default decision

If the product needs a license, a regulator approval, or a lawyer, the fleet does not build it.
The fleet picks another pain from the pain report.

## Health data

- Does the product store or process health data about a person? If yes, treat it as special category data.
- Is a medical device or a diagnosis involved? If yes, stop. Do not build it.
- Can the product work with no health data at all? If yes, design it that way.
- If health data stays, is there a lawful basis, a privacy notice, and a delete path? All three are required.

## Children

- Is the product made for people under 13, or under 16 in the EU? If yes, stop. Do not build it.
- Could children reach the product by chance? If yes, add an age gate and a notice that the product is not for children.
- Never collect data from a person who says they are a child. Delete it when you find it.

## Financial or legal advice

- Does the product give advice about money, taxes, investments, or the law for a specific person? If yes, stop.
- General education is allowed. Label it as education. Add a line that it is not advice.
- Never promise returns or outcomes. Never claim a license that the fleet does not hold.

## Payments for others

- Does the product move money between other people, hold money for them, or lend money? If yes, stop.
- Selling the product itself through a payment provider is allowed. The owner sets up the payment account.
- Never store card numbers. Let the payment provider handle them.

## Personal data at scale

- Does the product collect names, emails, or other personal data from many people? List each field and why it is needed.
- Collect the least data possible. Do not keep data longer than needed.
- Provide a privacy notice, a way to ask for deletion, and a way to export data.
- Never sell personal data. Never send it to a model provider that may train on prompts.
- If you serve people in the EU or California, the privacy notice names the rights those laws give.

## Always

- Keep the list of answers in the project folder. The briefing may cite it.
- Update the answers when the product changes.
- When a law or platform rule changes, run the checklist again.
