/* mtp_send — push one file to a Garmin watch over MTP, with BOTH ids set.
 *
 * WHY THIS EXISTS
 * ---------------
 * `mtp-sendfile` fails on Garmin devices with:
 *     get_suggested_storage_id(): could not get storage id from parent id
 * libmtp cannot resolve the parent folder to a storage on these devices, so
 * the send is refused before it starts. The fix is to set `parent_id` AND
 * `storage_id` explicitly on the file metadata, which the stock CLI has no
 * flag for.
 *
 * garmin/README.md has described this fix since 2026-08-10 ("~30 lines of C
 * against libmtp.h") and the code was never actually committed — so every
 * sideload re-derived it. Tonight the owner's brother is here for ~90 minutes
 * with the watch that has never been sideloaded, and rediscovering this under
 * time pressure would be the single most expensive way to spend that window.
 * Writing it down as CODE instead of prose is the whole lesson of this repo.
 *
 * BUILD (no pkg-config on this Mac):
 *   cc -O2 -o tools/mtp_send tools/mtp_send.c \
 *      -I/opt/homebrew/opt/libmtp/include -L/opt/homebrew/opt/libmtp/lib -lmtp
 *
 * USAGE:
 *   tools/mtp_send --list                      # storages + Apps folder ids
 *   tools/mtp_send <file> <parent_id> [storage_id]
 *
 * Storage on the Epix Gen 2 is a single read/write 0x00020001. The Instinct
 * 3 Solar has NEVER been sideloaded from this Mac and its ids are unknown —
 * run --list with the watch attached and read them off, do not assume the
 * Epix's values carry over. That assumption is exactly the kind this project
 * keeps paying for.
 *
 * VERIFY AFTER SENDING — a transfer that "returns 0" is not proof:
 *   mtp-files | grep -A6 'File ID: <id>'   # size matches, Parent ID is Apps
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <libmtp.h>

static void list_targets(LIBMTP_mtpdevice_t *dev) {
    LIBMTP_devicestorage_t *s;
    printf("STORAGES\n");
    for (s = dev->storage; s != NULL; s = s->next) {
        printf("  storage_id 0x%08X  %s  (free %llu of %llu bytes)\n",
               s->id,
               s->StorageDescription ? s->StorageDescription : "(no description)",
               (unsigned long long)s->FreeSpaceInBytes,
               (unsigned long long)s->MaxCapacity);
    }

    printf("\nFOLDERS (looking for GARMIN/Apps — that is where .prg files go)\n");
    for (s = dev->storage; s != NULL; s = s->next) {
        LIBMTP_folder_t *root = LIBMTP_Get_Folder_List_For_Storage(dev, s->id);
        LIBMTP_folder_t *stack[512];
        int top = 0;
        if (root) stack[top++] = root;
        while (top > 0) {
            LIBMTP_folder_t *f = stack[--top];
            if (f->name && (strcasecmp(f->name, "Apps") == 0 ||
                            strcasecmp(f->name, "GARMIN") == 0)) {
                printf("  folder_id %u  name '%s'  parent %u  storage 0x%08X\n",
                       f->folder_id, f->name, f->parent_id, s->id);
            }
            if (f->sibling && top < 511) stack[top++] = f->sibling;
            if (f->child   && top < 511) stack[top++] = f->child;
        }
        if (root) LIBMTP_destroy_folder_t(root);
    }
    printf("\nSend with:  tools/mtp_send <file> <Apps folder_id> <storage_id>\n");
}

int main(int argc, char **argv) {
    LIBMTP_Init();

    LIBMTP_mtpdevice_t *dev = LIBMTP_Get_First_Device();
    if (dev == NULL) {
        fprintf(stderr, "no MTP device found — is the watch plugged in and "
                        "unlocked? (mtp-detect should list it)\n");
        return 1;
    }

    if (argc >= 2 && strcmp(argv[1], "--list") == 0) {
        list_targets(dev);
        LIBMTP_Release_Device(dev);
        return 0;
    }

    if (argc < 3) {
        fprintf(stderr,
                "usage: %s --list\n"
                "       %s <file> <parent_id> [storage_id]\n", argv[0], argv[0]);
        LIBMTP_Release_Device(dev);
        return 2;
    }

    const char *path = argv[1];
    uint32_t parent  = (uint32_t)strtoul(argv[2], NULL, 0);
    uint32_t storage = (argc >= 4) ? (uint32_t)strtoul(argv[3], NULL, 0)
                                   : (dev->storage ? dev->storage->id : 0);

    struct stat st;
    if (stat(path, &st) != 0) {
        fprintf(stderr, "cannot stat %s\n", path);
        LIBMTP_Release_Device(dev);
        return 1;
    }

    const char *base = strrchr(path, '/');
    base = base ? base + 1 : path;

    LIBMTP_file_t *f = LIBMTP_new_file_t();
    f->filesize   = (uint64_t)st.st_size;
    f->filename   = strdup(base);
    f->filetype   = LIBMTP_FILETYPE_UNKNOWN;
    f->parent_id  = parent;    /* the two fields the stock CLI cannot set */
    f->storage_id = storage;

    printf("sending %s (%lld bytes) -> parent %u, storage 0x%08X\n",
           base, (long long)st.st_size, parent, storage);

    int rc = LIBMTP_Send_File_From_File(dev, path, f, NULL, NULL);
    if (rc != 0) {
        fprintf(stderr, "SEND FAILED\n");
        LIBMTP_Dump_Errorstack(dev);
        LIBMTP_Clear_Errorstack(dev);
    } else {
        /* Report the id so the caller can verify the push actually landed —
         * a zero return is not evidence the bytes are on the watch. */
        printf("OK — new file id %u. VERIFY IT:\n", f->item_id);
        printf("   mtp-files | grep -A6 'File ID: %u'\n", f->item_id);
        printf("   (size must match %lld and Parent ID must be %u)\n",
               (long long)st.st_size, parent);
    }

    LIBMTP_destroy_file_t(f);
    LIBMTP_Release_Device(dev);
    return rc == 0 ? 0 : 1;
}
