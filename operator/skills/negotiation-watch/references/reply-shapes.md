# negotiation-watch: reply shapes

The skill sends exactly one kind of message: a notice's composed text, to the
recipient and under the subject the broker binds. Everything below is the
complete set.

## The one message (DELIVER, outcome `notice`)

Recipient and subject: the broker's (`reply_bind` with the job id; mode
`new_message`). The body is the notice's `message`, word for word, then:

```
Thanks,
```

Specimens of a composed `message` (invented names and numbers):

```
New offer on matter 200123, Doe v. Example.
Example Mutual offer of $15,000 dated 10/7/26, per 'Offer letter 10-07.pdf' letter.
Entered in Negotiation Details, row 3.
```

```
New offer on matter 200123, Doe v. Example.
Example Mutual offer of $15,000 as read (not confirmed in the letter) dated 10/7/26, per 'RE Offer.msg' email.
Entered in Negotiation Details, row 3, with the date only: the amount could not be confirmed from the letter, please check it.
```

```
New offer on matter 200123, Doe v. Example.
Example Mutual offer of $15,000 dated 10/7/26, per 'Offer letter 10-07.pdf' letter.
Not entered in Negotiation Details, please check the letter: the same amount is already on row 2 with a different date.
```

## Never sent

- Anything on a `failed` wake (SMD's shortfall alert carries it).
- Anything after a refused bind.
- A second message for the same notice, a digest, a follow-up, or a message to
  anyone other than the bound recipient.
- A reworded, summarized or rounded version of the composed message.
