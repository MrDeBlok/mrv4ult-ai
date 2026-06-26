# MRV4ULT AI - Product Requirements Document (PRD)

## Project Vision

MRV4ULT AI is an AI assistant built specifically for luxury watch brokers.

The goal is to save time, prevent missed opportunities, and automatically match watch offers with client requests.

The AI should work in the background while the broker continues using WhatsApp as usual.

---

## Version 1 Goal

The first version must be able to:

* Read a WhatsApp message.
* Detect whether it contains watch offers or watch requests.
* Split offer lists into individual watches.
* Extract structured watch data.
* Store the extracted data.
* Detect duplicate offers.
* Match new offers with active client requests.

---

## User Workflow

1. A WhatsApp message is received.
2. MRV4ULT AI reads the message.
3. The AI determines:

   * Offer
   * Request
   * Unknown
4. The AI extracts all watch information.
5. Duplicate detection runs.
6. The offer is stored.
7. Active client requests are checked.
8. If a match exists, notify the user.

---

## Future Versions

Future versions should support:

* Images
* PDFs
* Voice notes
* Dealer intelligence
* Market price analysis
* Profit estimation
* Automatic WhatsApp integration
* Dashboard
* Search engine
* Client management
* Dealer management
