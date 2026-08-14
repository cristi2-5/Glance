/**
 * The reading journal for one book: dated notes, oldest first, plus a
 * composer for the next one.
 *
 * ## Why oldest first
 *
 * This is the one list in the app that is *not* newest-first. A journal's
 * value is watching an opinion move between chapter three and the last
 * page; putting the conclusion above the doubt that produced it destroys
 * exactly the thing worth keeping.
 *
 * ## Why notes are separate rows, not one field
 *
 * Module 6a shipped a single note per book, asked for on the scan result
 * screen. That was wrong twice: it was requested seconds after the cover
 * was photographed, when the reader has nothing to say, and it could hold
 * only one thought, overwritten each time. A book read over two weeks
 * produces several, and the interesting ones are usually not the last.
 *
 * Nothing written here ever reaches the RAG corpus — see the backend
 * model. A summary that quoted the reader back at themselves, formatted
 * as criticism and correctly cited, would be indistinguishable from real
 * sourcing.
 */

import { Feather } from '@expo/vector-icons'
import { useState } from 'react'
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, TextInput, View } from 'react-native'

import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import {
  useAddJournalEntry,
  useEditJournalEntry,
  useJournal,
  useRemoveJournalEntry,
} from '@/features/library/hooks'
import { colors, radius, space, textFit, typography } from '@/theme'
import type { JournalEntry } from '@/types/biblioteca'

interface JournalTimelineProps {
  bookId: number
}

/** Longest note the backend accepts, mirrored so the field stops there. */
const MAX_LENGTH = 5000

export function JournalTimeline({ bookId }: JournalTimelineProps) {
  const { data: notes, isPending, error } = useJournal(bookId)
  const add = useAddJournalEntry()

  const [draft, setDraft] = useState('')

  function submit() {
    const text = draft.trim()
    if (!text) {
      return
    }
    add.mutate(
      { bookId, content: text },
      {
        // Cleared only once the note is stored. Clearing on submit would
        // lose what the reader wrote if the request failed.
        onSuccess: () => {
          setDraft('')
        },
      },
    )
  }

  return (
    <View style={styles.section}>
      <Text {...textFit.sectionTitle} style={styles.heading}>
        Your journal
      </Text>

      {isPending ? (
        <ActivityIndicator color={colors.espresso} />
      ) : error ? (
        <Text {...textFit.prose} style={styles.error}>
          {error.message}
        </Text>
      ) : notes && notes.length > 0 ? (
        <View style={styles.timeline}>
          {notes.map((note) => (
            <JournalNote bookId={bookId} key={note.id} note={note} />
          ))}
        </View>
      ) : (
        <Text {...textFit.prose} style={styles.empty}>
          Nothing written yet. Notes stay in order, so you can come back mid-book and again at
          the end.
        </Text>
      )}

      <Card style={styles.composer}>
        <TextInput
          maxLength={MAX_LENGTH}
          multiline
          onChangeText={setDraft}
          placeholder="What are you thinking?"
          placeholderTextColor={colors.inkFaint}
          style={styles.input}
          value={draft}
        />

        <View style={styles.composerFooter}>
          <Text {...textFit.micro} style={styles.privacy}>
            PRIVATE — NEVER USED IN GENERATED SUMMARIES
          </Text>
          <Button
            disabled={add.isPending || draft.trim().length === 0}
            label={add.isPending ? 'Saving…' : 'Add note'}
            onPress={submit}
          />
        </View>

        {add.error ? (
          <Text {...textFit.prose} style={styles.error}>
            {add.error.message}
          </Text>
        ) : null}
      </Card>
    </View>
  )
}

/** One note in the timeline, with inline editing and delete. */
function JournalNote({ bookId, note }: { bookId: number; note: JournalEntry }) {
  const edit = useEditJournalEntry()
  const remove = useRemoveJournalEntry()

  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(note.content)

  function save() {
    const trimmed = text.trim()
    if (!trimmed) {
      return
    }
    edit.mutate(
      { bookId, entryId: note.id, content: trimmed },
      {
        onSuccess: () => {
          setEditing(false)
        },
      },
    )
  }

  function confirmDelete() {
    Alert.alert('Delete this note?', 'It will be gone for good.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: () => {
          remove.mutate({ bookId, entryId: note.id })
        },
      },
    ])
  }

  return (
    <Card style={styles.note}>
      <View style={styles.noteHeader}>
        <Text {...textFit.micro} style={styles.noteDate}>
          {formatNoteDate(note.created_at)}
          {wasEdited(note) ? ' · EDITED' : ''}
        </Text>

        <View style={styles.noteActions}>
          <Pressable
            accessibilityLabel={editing ? 'Cancel editing' : 'Edit note'}
            accessibilityRole="button"
            hitSlop={space[2]}
            onPress={() => {
              // Reset the draft on cancel, so an abandoned edit doesn't
              // reappear the next time the note is opened.
              setText(note.content)
              setEditing(!editing)
            }}
          >
            <Feather color={colors.inkFaint} name={editing ? 'x' : 'edit-2'} size={16} />
          </Pressable>

          <Pressable
            accessibilityLabel="Delete note"
            accessibilityRole="button"
            hitSlop={space[2]}
            onPress={confirmDelete}
          >
            <Feather color={colors.inkFaint} name="trash-2" size={16} />
          </Pressable>
        </View>
      </View>

      {editing ? (
        <>
          <TextInput
            autoFocus
            maxLength={MAX_LENGTH}
            multiline
            onChangeText={setText}
            style={styles.input}
            value={text}
          />
          <Button
            disabled={edit.isPending || text.trim().length === 0}
            label={edit.isPending ? 'Saving…' : 'Save'}
            onPress={save}
          />
        </>
      ) : (
        <Text {...textFit.prose} style={styles.noteBody}>
          {note.content}
        </Text>
      )}
    </Card>
  )
}

/**
 * Whether a note has been edited since it was written.
 *
 * Compared with a one-second tolerance: `created_at` and `updated_at` are
 * set from two separate `datetime.utcnow()` calls on insert, so they are
 * almost never bit-identical, and a strict comparison would label every
 * note "edited" the moment it was created.
 *
 * Args:
 *   note: The note to check.
 */
function wasEdited(note: JournalEntry): boolean {
  const created = new Date(note.created_at).getTime()
  const updated = new Date(note.updated_at).getTime()
  if (Number.isNaN(created) || Number.isNaN(updated)) {
    return false
  }
  return updated - created > 1000
}

/**
 * Formats a note's timestamp as "14 AUGUST 2026".
 *
 * Args:
 *   iso: The ISO 8601 timestamp.
 */
function formatNoteDate(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return ''
  }
  return date
    .toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
    .toUpperCase()
}

const styles = StyleSheet.create({
  section: {
    gap: space[3],
  },
  heading: {
    ...typography.displaySmall,
    color: colors.ink,
  },
  timeline: {
    gap: space[3],
  },
  note: {
    gap: space[2],
    padding: space[4],
  },
  noteHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: space[3],
  },
  noteDate: {
    ...typography.micro,
    color: colors.inkFaint,
    // `textColumn`: without it a long date pushes the actions off the row.
    flex: 1,
    minWidth: 0,
  },
  noteActions: {
    flexDirection: 'row',
    gap: space[4],
  },
  noteBody: {
    ...typography.bodyReading,
    color: colors.ink,
  },
  composer: {
    gap: space[3],
    padding: space[4],
  },
  input: {
    ...typography.body,
    color: colors.ink,
    backgroundColor: colors.paperSunken,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.md,
    paddingHorizontal: space[3],
    paddingVertical: space[3],
    // `minHeight`, never `height`: a fixed height turns OS font scaling
    // into clipped text.
    minHeight: space[9],
    textAlignVertical: 'top',
  },
  composerFooter: {
    gap: space[3],
  },
  privacy: {
    ...typography.micro,
    color: colors.inkFaint,
  },
  empty: {
    ...typography.body,
    color: colors.inkMuted,
  },
  error: {
    ...typography.body,
    color: colors.danger,
  },
})
