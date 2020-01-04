#!/usr/bin/python

import praw
import time
from datetime import datetime
import re
import logging


# global data
SUBREDDIT_LIST = []
COMMENT_IDS = []
SUBMISSION_IDS = []
QUEUE_IDS = []
LOG_IDS = []
FREQUENCY = {
    "load_subreddits": 3600,
    "check_comments": 5,
    "check_submissions": 30,
    "check_queue": 60,
    "check_mail": 120,
    "check_contributions": 60,
    "sync_friends": 300,
}
LAST = {}
BAN_TEMPLATE = ("Bots are not welcome on /r/{}.\n\n"
                "[I am a bot, and this action was performed automatically]"
                "(/r/BotDefense/about/sticky). "
                "If you wish to dispute whether this account is a bot, please "
                "[contact the moderators of /r/BotDefense]"
                "(https://www.reddit.com/message/compose?"
                "to=/r/BotDefense&subject=Ban%20dispute%20for%20/u/{}%20on%20/r/{}).")


# setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(funcName)s | %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
)
praw_config = {"user_agent": "linux:botdefense:v1.0 (by /u/BotDefense)"}
r = praw.Reddit("botdefense", **praw_config)


def ready(key, force=False):
    global LAST

    if force or LAST.get(key, 0) < time.time() - FREQUENCY.get(key, 60):
        LAST[key] = time.time()
        return 1
    return 0


def load_subreddits(force=False):
    global SUBREDDIT_LIST

    if not ready("load_subreddits", force=force):
        return

    logging.info("loading subreddits")
    SUBREDDIT_LIST = []
    # use this method to avoid 100 subreddit limit for now
    mod_target = "/user/" + str(r.user.me()) + "/moderated_subreddits"
    for subreddit in r.get(mod_target)['data']:
        name = subreddit['sr']
        SUBREDDIT_LIST.append(r.subreddit(name).display_name)


def check_comments():
    global COMMENT_IDS

    if not ready("check_comments"):
        return

    logging.info("checking comments")
    for comment in r.subreddit("friends").comments(limit=100):
        if str(comment.id) in COMMENT_IDS:
            continue
        link = "https://www.reddit.com/comments/{}/_/{}".format(comment.submission.id, comment.id)
        consider_action(comment, link)
        # we only cache identifiers after processing successfully
        COMMENT_IDS.append(str(comment.id))

    # trim cache
    if len(COMMENT_IDS) > 200:
        COMMENT_IDS = COMMENT_IDS[100:]


def check_submissions():
    global SUBMISSION_IDS

    if not ready("check_submissions"):
        return

    logging.info("checking submissions")
    for submission in r.subreddit("friends").new(limit=100):
        if str(submission.id) in SUBMISSION_IDS:
            continue
        link = "https://www.reddit.com/comments/" + str(submission.id)
        consider_action(submission, link)
        # we only cache identifiers after processing successfully
        SUBMISSION_IDS.append(str(submission.id))

    # trim cache
    if len(SUBMISSION_IDS) > 200:
        SUBMISSION_IDS = SUBMISSION_IDS[100:]


def check_queue():
    global QUEUE_IDS

    if not ready("check_queue"):
        return

    logging.info("checking queue")
    friends = r.user.friends()
    for submission in r.subreddit("mod").mod.modqueue(limit=100, only="submissions"):
        if str(submission.id) in QUEUE_IDS:
            continue
        if submission.author in friends:
            link = "https://www.reddit.com/comments/" + str(submission.id)
            logging.info("queue hit for /u/{} in /r/{} at {}".format(submission.author, submission.subreddit, link))
            consider_action(submission, link)
        # we only cache identifiers after processing successfully
        QUEUE_IDS.append(str(submission.id))

    # trim cache
    if len(QUEUE_IDS) > 200:
        QUEUE_IDS = QUEUE_IDS[100:]


def consider_action(post, link):
    sub = str(post.subreddit)
    author = str(post.author)

    if sub not in SUBREDDIT_LIST:
        return

    logging.info("subreddit hit /u/{} in /r/{}".format(author, sub))

    permissions = []
    try:
        permissions = post.subreddit.moderator("BotDefense")[0].mod_permissions
    except Exception as e:
        logging.error("error checking moderator permissions in /r/{}: {}".format(sub, e))

    is_friended = False
    if is_friend(author):
        is_friended = True
        logging.info("/u/{} confirmed to be on friends list".format(author))
    else:
        logging.error("/u/{} is not on friends list".format(author))

    is_proof_flaired = False
    try:
        if str(post.author_flair_css_class).endswith("proof"):
            is_proof_flaired = True
            logging.info("/u/{} is whitelisted via flair class in /r/{}".format(author, sub))
    except Exception as e:
        logging.error("error checking flair class for /u/{} in /r/{}: {}".format(author, sub, e))

    is_contributor = False
    try:
        for contributor in post.subreddit.contributor(author):
            is_contributor = True
            logging.info("/u/{} is whitelisted via approved users in /r/{}".format(author, sub))
    except Exception as e:
        logging.info("unable to check approved users for /u/{} in /r/{}: {}".format(author, sub, e))
        # fail safe
        if not permissions or "access" in permissions or "all" in permissions:
            is_contributor = True
            logging.error("failing safe for /u/{} in /r/{}".format(author, sub))

    is_moderator = False
    try:
        if post.subreddit.moderator(author):
            is_moderator = True
            logging.info("/u/{} is whitelisted via moderator list in /r/{}".format(author, sub))
    except Exception as e:
        # fail safe
        is_moderator = True
        logging.error("error checking moderator list, failing safe for /u/{} in /r/{}: {}".format(author, sub, e))

    if is_friended and not is_proof_flaired and not is_contributor and not is_moderator:
        if "access" in permissions or "all" in permissions:
            ban(author, sub, link, ("mail" in permissions))
        if "posts" in permissions or "all" in permissions:
            try:
                if not (post.removed or post.spam):
                    logging.info("removing " + link)
                    post.mod.remove(spam=True)
            except Exception as e:
                logging.error("error removing {}: {}".format(link, e))


def is_friend(user):
    try:
        if type(user) == str:
            return r.redditor(user).is_friend
        else:
            return user.is_friend
    except Exception as e:
        logging.debug("exception checking is_friend for /u/{}: {}".format(user, e))
    try:
        return r.get("/api/v1/me/friends/" + str(user)) == str(user)
    except Exception as e:
        logging.debug("exception checking friends for /u/{}: {}".format(user, e))
    try:
        return user in r.user.friends()
    except Exception as e:
        logging.error("failed searching friends for /u/{}: {}".format(user, e))
    return None


def ban(author, sub, link, mute):
    already_banned = False
    logging.info("banning /u/{} in /r/{}".format(author, sub))
    try:
        for ban in r.subreddit(sub).banned(author):
            logging.info("/u/{} already banned in /r/{}".format(author, sub))
            already_banned = True
    except Exception as e:
        logging.error("error banning /u/{} in /r/{}: {}".format(author, sub, e))
    if not already_banned:
        date = str(datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d"))
        try:
            r.subreddit(sub).banned.add(
                author, ban_message=BAN_TEMPLATE.format(sub, author, sub),
                note="/u/{} banned by /u/BotDefense at {} for {}".format(author, date, link))
            logging.info("banned /u/{} in /r/{}".format(author, sub))
            if mute:
                logging.info("muting /u/{} in /r/{}".format(author, sub))
                r.subreddit(sub).muted.add(author)
        except Exception as e:
            logging.error("error banning /u/{}: {}".format(author, e))


def unban(account):
    for subreddit in SUBREDDIT_LIST:
        try:
            for ban in r.subreddit(subreddit).banned(account):
                try:
                    if ban.note and re.search("BotDefense", ban.note):
                        logging.info("unbanning /u/{} on /r/{} ({})".format(account, subreddit, ban.note))
                        r.subreddit(subreddit).banned.remove(account)
                    else:
                        note = ban.note
                        if not note or len(note) == 0:
                            note = "[empty]"
                        logging.debug("not unbanning /u/{} on /r/{} ({})".format(account, subreddit, note))
                except Exception as e:
                    logging.error("exception unbanning /u/{} on /r/{}".format(account, subreddit))
        except:
            # we could check permissions, but this seems sufficient
            logging.warning("unable to check ban for /u/{} on /r/{}".format(account, subreddit))


def check_contributions():
    if not ready("check_contributions"):
        return

    logging.info("checking for contributions")
    for submission in r.subreddit("BotDefense").new(limit=100):
        if submission.author == "BotDefense":
            continue

        # non-conforming posts are removed by AutoModerator so just skip them
        account = ""
        repost = False
        post = None
        if submission.url:
            m = re.search(
                "^https?://(?:\w+\.)?reddit\.com/(?:u|user)/([\w-]+)", submission.url
            )
            if m:
                account = m.group(1)

        if account and len(account) > 0:
            try:
                user = r.redditor(name=account)
                user_data = r.get(
                    "/api/user_data_by_account_ids", {"ids": user.fullname}
                )
                name = user_data[user.fullname]["name"]
                if name and len(name) > 0:
                    title = "overview for " + name
                    url = "https://www.reddit.com/user/" + name
                    for query in "url:" + url, "title" + name:
                        for similar in r.subreddit("BotDefense").search(query):
                            if similar.title == title and similar.author == "BotDefense":
                                repost = True
                                break
                        if repost:
                            break
                    if not repost:
                        for recent in r.subreddit("BotDefense").new(limit=1000):
                            if recent.title == title and recent.author == "BotDefense":
                                repost = True
                                break
                            if recent.created_utc < time.time() - 604800:
                                break
                    if not repost:
                        post = r.subreddit("BotDefense").submit(title, url=url)
                        post.disable_inbox_replies()

            except Exception as e:
                logging.error("exception creating canonical post: " + str(e))

        if post:
            submission.mod.remove()
            comment = submission.reply("Thank you for your submission!")
            comment.mod.distinguish()
            logging.info("contribution {} accepted for {}".format(submission.permalink, name))
        elif repost:
            submission.mod.remove()
            comment = submission.reply(
                "Thank you for your submission! It looks like we already have an"
                " entry for that account!"
            )
            comment.mod.distinguish()
            logging.info("contribution {} duplicate for {}".format(submission.permalink, name))
        elif account and len(account) > 0:
            submission.mod.remove()
            comment = submission.reply(
                "Thank you for your submission! That account does not appear to"
                " exist (perhaps it has already been suspended, banned, or deleted),"
                " but please send modmail if you believe this was an error."
            )
            comment.mod.distinguish()
            logging.info("contribution {} rejected".format(submission.permalink))


def check_mail():
    if not ready("check_mail"):
        return

    logging.info("checking mail")
    for message in r.inbox.unread(limit=10):
        sender = str(message.author)

        # skip reddit messages and non-messages
        if sender == "reddit" or not message.fullname.startswith("t4_"):
            message.mark_read()
            continue
        # skip non-subreddit messages
        if not message.subreddit:
            message.reply("Please modmail /r/BotDefense if you would like to get in touch.")
            message.mark_read()
            continue

        sub = message.subreddit.display_name

        # looks like an invite
        if re.search("^invitation to moderate /?(r|u|user)/[\w-]+$", str(message.subject)):
            logging.info("invited to moderate /r/{}".format(sub))
            result = None
            reason = None

            try:
                result, reason = join_subreddit(message.subreddit)
            except:
                reason = "error"

            if result:
                message.mark_read()
                logging.info("joined /r/{}".format(sub))
            else:
                message.mark_read()
                if reason == "error":
                    logging.info("failure accepting invite {} from /r/{}".format(message.fullname, sub))
                elif reason:
                    logging.info("declining invite from {} subreddit /r/{}".format(reason, sub))
                    message.reply(
                        "This bot isn't really needed on non-public subreddits due to very limited bot activity."
                        " If you believe this was sent in error, please modmail /r/BotDefense."
                    )
        # looks like a removal
        elif re.search("^/?u/[\w-]+ has been removed as a moderator from /?(r|u|user)/[\w-]+$", str(message.subject)):
            message.mark_read()
            load_subreddits(force=True)
            if sub not in SUBREDDIT_LIST:
                logging.info("removed as moderator from /r/" + sub)
        # some other type of subreddit message
        else:
            message.mark_read()


def join_subreddit(subreddit):
    if subreddit.quarantine:
        return False, "quarantined"
    elif subreddit.subreddit_type not in ["public", "restricted", "gold_only", "user"]:
        return False, subreddit.subreddit_type

    try:
        subreddit.mod.accept_invite()
        SUBREDDIT_LIST.append(subreddit.display_name)
    except Exception as e:
        logging.error("exception joining /r/{}: {}".format(subreddit.display_name, e))
        return False, "error"
    else:
        return True, None

def sync_friends():
    global LOG_IDS

    if not ready("sync_friends"):
        return

    logging.info("syncing friends")
    try:
        for log in r.subreddit("BotDefense").mod.log(action="editflair", limit=100):
            # only log ids can be persistently cached, not submission ids
            if str(log.id) in LOG_IDS:
                continue
            try:
                if log.target_author == "BotDefense" and log.target_fullname.startswith("t3_"):
                    submission = r.submission(id=log.target_fullname[3:])
                    sync_submission(submission)
                # we only cache log identifiers after processing successfully
                LOG_IDS.append(str(log.id))
            except Exception as e:
                logging.info("exception processing log {}: ".format(log.id, e))
    except Exception as e:
        logging.info("exception syncing friends: " + str(e))

    # trim cache
    if len(LOG_IDS) > 200:
        LOG_IDS = LOG_IDS[100:]


def sync_submission(submission):
    account = None
    if submission.url:
        m = re.search("/(?:u|user)/([\w-]+)", submission.url)
        if m:
            account = str(m.group(1))
    if account and submission.link_flair_text != "pending":
        is_friended = is_friend(account)
        if is_friended is None:
            logging.info("skipping " + account)
        elif is_friended is False and submission.link_flair_text == "banned":
            logging.info("adding friend " + account)
            r.redditor(account).friend()
        elif is_friended and submission.link_flair_text != "banned":
            logging.info("removing friend " + account)
            r.redditor(account).unfriend()
            unban(account)


def run():
    load_subreddits()
    check_comments()
    check_submissions()
    check_queue()
    check_mail()
    check_contributions()
    sync_friends()
    time.sleep(1)


if __name__ == "__main__":
    while True:
        try:
            run()
        except KeyboardInterrupt:
            logging.error("received SIGINT from keyboard, stopping")
            exit(1)
        except Exception as e:
            logging.error("site error: " + str(e))
            time.sleep(300)
