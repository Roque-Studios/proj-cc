"""Default legal documents shown to subscribers before checkout.

Every creator gets a working Terms of Service and Privacy Policy out of the
box: when a creator hasn't customized their own texts (``CreatorProfile.
tos_text`` / ``privacy_text`` are NULL or blank), these platform defaults are
served instead. The texts are drafted for a subscription platform whose
content may be **AI-generated** — they disclose that fact, keep the license
personal and non-commercial, and cover the consent + age gate enforced at
checkout.

Creators may replace either document from the admin panel (the ``Legal`` tab
writes ``CreatorProfile.tos_text`` / ``privacy_text``); an empty value falls
back to these defaults so a creator can never leave subscribers without a
policy.
"""

from __future__ import annotations

DEFAULT_TOS = """TERMS OF SERVICE

Last updated: 2026-08-08

These Terms of Service ("Terms") form a binding agreement between you ("Subscriber", "you") and the creator whose content you are subscribing to ("Creator", "we", "us") governing your subscription to and use of the Creator's content (the "Service"). By completing checkout you accept these Terms.

1. ELIGIBILITY AND AGE VERIFICATION
By subscribing you represent and warrant that you are at least 18 years of age (or the age of majority in your jurisdiction, whichever is higher) and that you are legally capable of entering into this agreement. The Service is not directed to minors, and we do not knowingly accept subscriptions from anyone under 18. You confirm this again at every checkout before any payment is taken.

2. THE SERVICE AND AI-GENERATED CONTENT
The Service provides access to content published by the Creator, which may include text, images, audio, and other media. You acknowledge and agree that some or all of this content may be generated, in whole or in part, with the assistance of artificial intelligence (AI) tools, models, or processes. AI-generated content may contain inaccuracies, inconsistencies, or unexpected outputs and is provided for entertainment and informational purposes only. Your subscription grants you a revocable, non-exclusive, non-transferable, personal license to access the content for personal, non-commercial use. You obtain no ownership rights in any content.

3. PAYMENTS AND SUBSCRIPTION
Your subscription is billed monthly in advance at the price displayed at checkout. By completing checkout you authorize the selected payment provider to charge your payment method. Subscriptions renew automatically until canceled, and you may cancel at any time; access continues until the end of the current billing period. Because digital content is delivered instantly upon payment, all payments are final and non-refundable, except as required by applicable law.

4. ACCEPTABLE USE
You agree not to: (a) redistribute, resell, republish, download, record, or share the content, or any part of it, with any third party or on any platform; (b) use the content to train, fine-tune, or develop any artificial-intelligence model or machine-learning system; (c) circumvent any technical protection measure or access control; (d) use the Service for any unlawful purpose; or (e) impersonate the Creator or misrepresent your identity.

5. INTELLECTUAL PROPERTY
All content and all rights in it remain the property of the Creator or their licensors. No ownership is transferred to you, and the use of AI tools in creating content does not create any ownership right for you.

6. DISCLAIMER OF WARRANTIES
The Service is provided "as is" and "as available", without warranties of any kind, express or implied, including merchantability, fitness for a particular purpose, and non-infringement. AI-generated content may be inaccurate, offensive, or unsuitable, and you assume all risk of using it.

7. LIMITATION OF LIABILITY
To the maximum extent permitted by law, the Creator shall not be liable for any indirect, incidental, special, consequential, or punitive damages, or for any loss of profits, data, or goodwill, arising out of or relating to your use of the Service, even if advised of the possibility of such damages. The Creator's total liability shall not exceed the amount you paid for the subscription in the twelve (12) months preceding the claim.

8. TERMINATION
The Creator may suspend or terminate your access at any time for breach of these Terms or for any lawful reason. Upon termination, your right to access the content ends; the sections that by their nature survive (including intellectual property, disclaimers, limitation of liability, and governing law) continue to apply.

9. CHANGES TO THE SERVICE AND TERMS
We may modify the Service or these Terms from time to time. Material changes will be posted on the Creator's legal page. Continued use of the Service after changes take effect constitutes acceptance of the updated Terms.

10. GOVERNING LAW
These Terms are governed by the laws of the jurisdiction in which the Creator operates, without regard to conflict-of-law principles. Any disputes will be resolved in the competent courts of that jurisdiction.

11. CONTACT
Questions about these Terms may be directed to the Creator through the contact details provided on their profile page.
"""

DEFAULT_PRIVACY = """PRIVACY POLICY

Last updated: 2026-08-08

This Privacy Policy explains how the creator whose content you subscribe to ("we", "us") collects, uses, and protects personal information in connection with your subscription and use of the Service.

1. INFORMATION WE COLLECT
- Account information: the email address and username you provide when creating an account.
- Payment information: collected and processed by the third-party payment provider you choose at checkout (e.g. Stripe, PayPal, Wompi). We do not store full card numbers; your payment details are handled by the provider under its own privacy policy.
- Usage information: how you interact with the Service, including content accessed, device and browser information, IP address, and timestamps.

2. HOW WE USE YOUR INFORMATION
- To operate and deliver the Service, including authenticating your account and providing access to the content you are entitled to.
- To process and manage payments and subscriptions.
- To communicate with you about your subscription and important service updates.
- To improve, secure, and troubleshoot the Service, including fraud prevention.
- Where you have consented, to personalize your experience.

3. AI-GENERATED CONTENT AND YOUR DATA
Some or all content on the Service may be created with the assistance of artificial intelligence. Your account information may be used to deliver, personalize, and secure that content. We do not use your personal information to train third-party AI models unless you separately opt in, and we never sell your personal information.

4. PAYMENT PROCESSORS AND THIRD PARTIES
Payments are processed by the payment gateway you select at checkout. We share only the minimum information necessary (such as your email address) to complete a transaction. Each processor applies its own privacy and security practices, which are described in its own privacy policy.

5. COOKIES AND SIMILAR TECHNOLOGIES
We may use cookies, local storage, and similar technologies to keep you signed in, remember preferences, and understand how the Service is used. You can control cookies through your browser settings, though some features may not work without them.

6. DATA RETENTION
We retain your personal information only as long as necessary to provide the Service and to comply with legal obligations, including records of payments and consents. When you cancel your subscription, account information is retained only as required by law or for legitimate business purposes and is otherwise deleted or anonymized.

7. YOUR RIGHTS
Depending on your jurisdiction (including under the GDPR or CCPA), you may have the right to access, correct, delete, or port your personal information, to object to or restrict certain processing, and to withdraw consent at any time. To exercise these rights, contact us using the details on the Creator's profile page.

8. SECURITY
We apply reasonable technical and organizational measures to protect your personal information. No method of transmission or storage is completely secure, and you use the Service at your own risk.

9. CHILDREN'S PRIVACY
The Service is intended for adults (18+) and is not directed to children under 13. We do not knowingly collect personal information from children. If you believe a child has provided us personal information, contact us and we will delete it.

10. CHANGES TO THIS POLICY
We may update this Privacy Policy from time to time. Material changes will be posted on the Creator's legal page. Your continued use of the Service after changes take effect constitutes acceptance of the updated policy.

11. CONTACT
For questions about this Privacy Policy or your personal information, contact the Creator through the details provided on their profile page.
"""
