import dateutil.parser
import glob
import os
import phonenumbers
import re
import time
from datetime import datetime, timedelta
from base64 import b64encode
from bs4 import BeautifulSoup
from io import open  # adds emoji support
from pathlib import Path
from shutil import copyfileobj, move
from tempfile import NamedTemporaryFile
from time import strftime

sms_backup_filename = "./gvoice-all.xml"
sms_backup_path = Path(sms_backup_filename)
# Clear file if it already exists
sms_backup_path.open("w").close()
print("New file will be saved to " + sms_backup_filename)

def main():
    start_time=datetime.now()
    print("Start time: ", start_time.strftime("%H:%M:%S"))
    remove_problematic_files()
    print("Checking directory for *.html files")
    num_sms = 0
    num_img = 0
    num_vcf = 0
    num_vid = 0
    root_dir = "."
    own_number = None

    # Create the src to filename mapping
    src_elements = extract_src(".")  # Assuming current directory
    att_filenames = list_att_filenames(".")    # Assuming current directory
    num_img = sum(1 for filename in att_filenames if Path(filename).suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif'})
    num_vcf = sum(1 for filename in att_filenames if Path(filename).suffix.lower() == '.vcf')
    num_vid = sum(1 for filename in att_filenames if Path(filename).suffix.lower() == '.mp4')
    src_filename_map = src_to_filename_mapping(src_elements, att_filenames)

    for subdir, dirs, files in os.walk(root_dir):
        for file in files:
            sms_filename = os.path.join(subdir, file)

            if os.path.splitext(sms_filename)[1] != ".html":
                #print(sms_filename,"- skipped")
                continue

            print("Processing " + sms_filename)

            is_group_conversation = re.match(r"(^Group Conversation)", file)

            with open(sms_filename, "r", encoding="utf8") as sms_file:
                soup = BeautifulSoup(sms_file, "html.parser")

            messages_raw = soup.find_all(class_="message")
            # Extracting own phone number if the <abbr> tag with class "fn" contains "Me"
            for abbr_tag in soup.find_all('abbr', class_='fn'):
                if abbr_tag.get_text(strip=True) == "Me":
                    a_tag = abbr_tag.find_previous('a', class_='tel')
                if a_tag:
                    own_number = a_tag.get('href').split(':', 1)[-1]  # Extracting number from href
                    break
            # Skip files with no messages
            if not len(messages_raw):
                continue

            num_sms += len(messages_raw)

            if is_group_conversation:
                participants_raw = soup.find_all(class_="participants")
                write_mms_messages(file, participants_raw, messages_raw, own_number, src_filename_map)
            else:
                write_sms_messages(file, messages_raw, own_number, src_filename_map)

    sms_backup_file = open(sms_backup_filename, "a")
    sms_backup_file.write("</smses>")
    sms_backup_file.close()
    end_time=datetime.now()
    elapsed_time = end_time - start_time
    total_seconds = int(elapsed_time.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours} hours")
    if minutes > 0:
        parts.append(f"{minutes} minutes")
    if seconds > 0 or (hours == 0 and minutes == 0):
        parts.append(f"{seconds} seconds")
    time_str = ", ".join(parts)
    print(f"Processed {num_sms} messages, {num_img} images, {num_vid} videos, and {num_vcf} contact cards in {time_str}")
    write_header(sms_backup_filename, num_sms)

def remove_problematic_files():
    #Get user confimration before deleteing files
    user_confirmation = input("""\

    Would you like to automatically remove conversations that won't convert?
    This is conversations without attached phone numbers, ones with shortcode phone numbers,
    or things like missed calls and voicemails.
    If you say yes, this will automatically delete those files before converting.
    (Y/n)? """)
    if user_confirmation == '' or user_confirmation == 'y' or user_confirmation == 'Y':
        # Find files starting with " -" instead of a phone number
        files_to_remove = glob.glob("Calls/ -*")
        # Remove each file
        for file in files_to_remove:
            try:
                os.remove(file)
                print(f"Removed no number conversation -- {file}")
            except OSError as e:
                print(f"Error removing no number conversation -- {file}: {e}")
        # Find files from a shortcode phonenumber or similar that don't import properly
        pattern = r'^[0-9]{1,8}.*$'
        subdirectory = './Calls'
        files = [os.path.join(f) for f in os.listdir(subdirectory) if os.path.isfile(os.path.join(subdirectory, f))]
        for file in files:
            if re.match(pattern, file):
                try:
                    file = os.path.join(subdirectory, file)
                    os.remove(file)
                    print(f"Removed shortcode conversation -- {file}")
                except OSError as e:
                    print(f"Error removing shortcode conversation -- {file}: {e}")
        return None
    else:
        return None

# Fixes special characters in the vCards
def escape_xml(s):
    return (s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("'", "&apos;")
            .replace('"', "&quot;"))

# Function to extract img src from HTML files
def extract_src(html_directory):
    src_list = []
    for html_file in Path(html_directory).rglob('*.html'):  # Assuming HTML files have .html extension
        with open(html_file, 'r', encoding='utf-8') as file:
            soup = BeautifulSoup(file, 'html.parser')
            src_list.extend([img['src'] for img in soup.find_all('img') if 'src' in img.attrs])
            src_list.extend([a['href'] for a in soup.find_all('a', class_='video') if 'href' in a.attrs])
            src_list.extend([a['href'] for a in soup.find_all('a', class_='vcard') if 'href' in a.attrs])
    return src_list

# Function to list attachment filenames with specific extensions
def list_att_filenames(image_directory):
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.vcf', '.mp4'}
    return [str(path.name) for path in Path(image_directory).rglob('*') 
            if path.suffix.lower() in allowed_extensions]

# Function to remove file extension and parenthesized numbers from the end of image filenames. This is used to match those filenames back to their respective img_src key.
def normalize_filename(filename):
    # Remove the file extension and any parenthesized numbers, then truncate at 50 characters
    return re.sub(r'(?:$$(\d+)$$)?\.(jpg|gif|png|vcf|mp4)$', '', filename)[:50]

# Function to sort filenames so that files with parenthesized numbers appended to the end follow the base filename.
def custom_filename_sort(filename):
    # This will match the entire filename up to the extension, and capture any numbers in parentheses
    match = re.match(r'(.*?)(?:$$(\d+)$$)?(\.\w+)?$', filename)
    if match:
        base_filename = match.group(1)
        number = int(match.group(2)) if match.group(2) else -1  # Assign -1 to filenames without parentheses
        extension = match.group(3) if match.group(3) else ''  # Some filenames may not have an extension
        return (base_filename, number, extension)
    else:
        # If there's no match (which should not happen with the filenames you provided),
        # return a tuple that sorts it last
        return (filename, float('inf'), '')

# Function to produce a dictionary that maps img src elements (which are unique) to the respective filenames.
def src_to_filename_mapping(src_elements, att_filenames):
    used_filenames = set()
    mapping = {}
    for src in src_elements:
        att_filenames.sort(key=custom_filename_sort)  # Sort filenames before matching
        assigned_filename = None
        for filename in att_filenames:
            normalized_filename = normalize_filename(filename)
            if normalized_filename in src and filename not in used_filenames:
                assigned_filename = filename
                used_filenames.add(filename)
                break
        mapping[src] = assigned_filename or 'No unused match found'
    return mapping

def write_sms_messages(file, messages_raw, own_number, src_filename_map):
    fallback_number = 0
    title_has_number = re.search(r"(^\+[0-9]+)", Path(file).name)
    if title_has_number:
        fallback_number = title_has_number.group()

    phone_number, participant_raw = get_first_phone_number(
        messages_raw, fallback_number
    )

    # Search similarly named files for a fallback number. This is desperate and expensive, but
    # hopefully rare.
    if phone_number == 0:
        file_prefix = "-".join(Path(file).stem.split("-")[0:1])
        for fallback_file in Path.cwd().glob(f"**/{file_prefix}*.html"):
            with fallback_file.open("r", encoding="utf8") as ff:
                soup = BeautifulSoup(ff, "html.parser")
            messages_raw_ff = soup.find_all(class_="message")
            phone_number, participant_raw = get_first_phone_number(messages_raw_ff, 0)
            if phone_number != 0:
                break

    # Start looking in the Placed/Received files for a fallback number
    if phone_number == 0:
        file_prefix = f'{Path(file).stem.split("-")[0]}- '
        for fallback_file in Path.cwd().glob(f"**/{file_prefix}*.html"):
            with fallback_file.open("r", encoding="utf8") as ff:
                soup = BeautifulSoup(ff, "html.parser")
            contrib_vcards = soup.find_all(class_="contributor vcard")
            phone_number_ff = 0
            for contrib_vcard in contrib_vcards:
                phone_number_ff = contrib_vcard.a["href"][4:]
            phone_number, participant_raw = get_first_phone_number([], phone_number_ff)
            if phone_number != 0:
                break

    sms_values = {"phone": phone_number}

    sms_backup_file = open(sms_backup_filename, "a", encoding="utf8")

    for message in messages_raw:
        # Check if message has an image or vCard in it and treat as mms if so
        if message.find_all("img"):
            write_mms_messages(file, [[participant_raw]], [message], own_number, src_filename_map)
            continue
        if message.find_all("a", class_='vcard'):
            write_mms_messages(file, [[participant_raw]], [message], own_number, src_filename_map)
            continue
        if message.find_all("a", class_='video'):
            write_mms_messages(file, [[participant_raw]], [message], own_number, src_filename_map)
            continue
        message_content = get_message_text(message)
        if message_content == "MMS Sent" or message_content == "MMS Received":
            continue
        sms_values["type"] = get_message_type(message)
        sms_values["message"] = message_content
        sms_values["time"] = get_time_unix(message)
        sms_text = (
            '<sms protocol="0" address="%(phone)s" '
            'date="%(time)s" type="%(type)s" '
            'subject="null" body="%(message)s" '
            'toa="null" sc_toa="null" service_center="null" '
            'read="1" status="1" locked="0" /> \n' % sms_values
        )
        sms_backup_file.write(sms_text)

    sms_backup_file.close()

def write_mms_messages(file, participants_raw, messages_raw, own_number, src_filename_map):
    sms_backup_file = open(sms_backup_filename, "a", encoding="utf8")

    participants = get_participant_phone_numbers(participants_raw)
    participants_text = "~".join(participants)

    # Adding own_number to participants if it exists and is not already in the list
    
    for message in messages_raw:
        # Sometimes the sender tel field is blank. Try to guess the sender from the participants.
        sender = get_mms_sender(message, participants)
        sent_by_me = sender==own_number
        if own_number not in participants:
            participants.append(own_number)
        
        # Handle images and vcards
        images = message.find_all("img")
        image_parts = ""
        videos = message.find_all("a", class_='video')
        video_parts = ""
        vcards = message.find_all("a", class_='vcard')
        vcard_parts = ""
        extracted_url = ""
        if images:
            text_only=0
            for image in images:
                # I have only encountered jpg and gif, but I have read that GV can ecxport png
                supported_types = ["jpg", "png", "gif", "webp", "heic"]
                image_src = image["src"]
                # Change to use the src_filename_map to find the image filename that corresponds to the image_src value, which is unique to each image MMS message.
                # Attempt to find a direct match for the image_src in the src_filename_map
                image_filename = src_filename_map.get(image_src)
                # If a direct match isn't found, prepend the start of the HTML file name to find the image name
                if image_filename is None or image_filename == "No unused match found":  # Adjust based on your actual "not found" condition
                    html_filename_prefix = file.split('-', 1)[0]
                    image_filename = html_filename_prefix + image_src[image_src.find('-'):]
                    image_filename_with_ext = f"{image_filename}.*"
                    image_path = list(Path.cwd().glob(f"**/{image_filename_with_ext}"))
                    image_path = [p for p in image_path if p.suffix[1:] in supported_types]
                else:
                    image_path = [p for p in Path.cwd().glob(f"**/*{image_filename}") if p.is_file()]                

                assert (
                    len(image_path) != 0
                ), f"No matching images found. File name: {image_filename}"
                assert (
                    len(image_path) == 1
                ), f"Multiple potential matching images found. Images: {[x for x in image_path]!r}"

                image_path = image_path[0]
                image_type = image_path.suffix[1:]
                image_type = "jpeg" if image_type in ["jpg", "webp"] else image_type

                with image_path.open("rb") as fb:
                    image_bytes = fb.read()
                byte_string = f"{b64encode(image_bytes)}"

                # Use the full path and then derive the relative path, ensuring the complete filename is used
                relative_image_path = image_path.relative_to(Path.cwd())
    
                image_parts += (
                    f'    <part seq="0" ct="image/{image_type}" name="{relative_image_path}" '
                    f'chset="null" cd="null" fn="null" cid="&lt;{relative_image_path}&gt;" '
                    f'cl="{relative_image_path}" ctt_s="null" ctt_t="null" text="null" '
                    f'data="{byte_string[2:-1]}" />\n'
                )
        if videos:
            text_only = 0
            for video in videos:
                supported_types = ["mp4"]
                video_src = video.get("href")
                
                # Print debug info
                print(f"Processing video with href: {video_src}")
                
                # Try multiple approaches to find the video file
                video_path = []
                
                # Approach 1: Direct match from src_filename_map
                video_filename = src_filename_map.get(video_src)
                if video_filename and video_filename != "No unused match found":
                    print(f"Found video in mapping: {video_filename}")
                    video_path = [p for p in Path.cwd().glob(f"**/*{video_filename}") if p.is_file() and p.suffix.lower() == '.mp4']
                
                # Approach 2: Try to extract the video filename from the href
                if not video_path:
                    # Extract just the filename part from the href
                    video_base_name = os.path.basename(video_src)
                    print(f"Looking for video with base name: {video_base_name}")
                    video_path = [p for p in Path.cwd().glob(f"**/*{video_base_name}*") if p.is_file() and p.suffix.lower() == '.mp4']
                
                # Approach 3: Try using the HTML filename prefix
                if not video_path:
                    html_filename_prefix = file.split('-', 1)[0]
                    # Try to find any video file that starts with the HTML filename prefix
                    print(f"Looking for video with HTML prefix: {html_filename_prefix}")
                    video_path = [p for p in Path.cwd().glob(f"**/{html_filename_prefix}*.mp4") if p.is_file()]
                
                # Approach 4: Last resort - look for any MP4 files in the directory
                if not video_path:
                    print("Last resort: Looking for any MP4 files")
                    video_path = [p for p in Path.cwd().glob("**/*.mp4") if p.is_file()]
                    # If we found multiple videos, try to find one that might match based on date/time in the filename
                    if len(video_path) > 1 and '-' in file:
                        date_part = file.split('-')[1].strip() if len(file.split('-')) > 1 else ""
                        if date_part:
                            print(f"Filtering videos by date part: {date_part}")
                            filtered_path = [p for p in video_path if date_part in p.name]
                            if filtered_path:
                                video_path = filtered_path
                
                # If we still have multiple videos, just use the first one
                if len(video_path) > 1:
                    print(f"Warning: Multiple potential videos found: {[str(p) for p in video_path]}")
                    print(f"Using the first one: {video_path[0]}")
                    video_path = [video_path[0]]
                
                # If we found a video, process it
                if video_path:
                    video_path = video_path[0]
                    print(f"Using video path: {video_path}")
                    video_type = video_path.suffix[1:]
                    
                    try:
                        with video_path.open("rb") as fb:
                            video_bytes = fb.read()
                        byte_string = f"{b64encode(video_bytes)}"
                        
                        # Use the full path and then derive the relative path
                        relative_video_path = video_path.relative_to(Path.cwd())
                        
                        video_parts += (
                            f'    <part seq="0" ct="video/{video_type}" name="{relative_video_path}" '
                            f'chset="null" cd="null" fn="null" cid="&lt;{relative_video_path}&gt;" '
                            f'cl="{relative_video_path}" ctt_s="null" ctt_t="null" text="null" '
                            f'data="{byte_string[2:-1]}" />\n'
                        )
                    except Exception as e:
                        print(f"Error processing video file {video_path}: {e}")
                        # Continue without the video attachment
                        continue
                else:
                    print(f"Warning: No video file found for {video_src}. Continuing without video attachment.")
                    # Continue without the video attachment
                    continue

        # Handle vcards
        if vcards:
            #continue
            text_only=0
            for vcard in vcards:
                # I have only encountered jpg and gif, but I have read that GV can ecxport png
                supported_types = ["vcf"]
                vcard_src = vcard.get("href")
                # Change to use the src_filename_map to find the vcards filename that corresponds to the vcards_src value, which is unique to each vcards MMS message.
                vcard_filename = src_filename_map.get(vcard_src)
                # If a direct match isn't found, prepend the start of the HTML file name to find the image name
                if vcard_filename is None or vcard_filename == "No unused match found":  # Adjust based on your actual "not found" condition
                    html_filename_prefix = file.split('-', 1)[0]
                    vcard_filename = html_filename_prefix + vcard_src[vcard_src.find('-'):]
                    vcard_filename_with_ext = f"{vcard_filename}.*"
                    vcard_path = list(Path.cwd().glob(f"**/{vcard_filename_with_ext}"))
                    vcard_path = [p for p in vcard_path if p.suffix[1:] in supported_types]
                else:
                    vcard_path = [p for p in Path.cwd().glob(f"**/*{vcard_filename}") if p.is_file()]                

                assert (
                    len(vcard_path) != 0
                ), f"No matching vCards found. File name: {vcard_filename}"
                assert (
                    len(vcard_path) == 1
                ), f"Multiple potential matching vCards found. vCards: {[x for x in vcard_path]!r}"

                vcard_path = vcard_path[0]
                vcard_type = vcard_path.suffix[1:]
                
                # This section searches for any contact cards that are just location pins, and turns them into a plain text MMS message with the URL for the pin.
                # If you don't want to perform this conversion, then comment out this section.
                with vcard_path.open("r", encoding="utf-8") as fb:
                    current_location_found = False
                    for line in fb:
                        if line.startswith("FN:") and "Current Location" in line:
                            current_location_found = True
                        if current_location_found and line.startswith("URL;type=pref:"):
                            extracted_url = line.split(":", 1)[1].strip()
                            extracted_url = extracted_url.replace("\\", "")  # Remove backslashes
                            extracted_url = escape_xml(extracted_url)
                            break

                    if not current_location_found:
                        with vcard_path.open("rb") as fb:
                            vcard_bytes = fb.read()
                            byte_string = f"{b64encode(vcard_bytes)}"

                            # Use the full path and then derive the relative path, ensuring the complete filename is used
                            relative_vcard_path = vcard_path.relative_to(Path.cwd())
    
                            vcard_parts += (
                            f'    <part seq="0" ct="text/x-vCard" name="{relative_vcard_path}" '
                            f'chset="null" cd="null" fn="null" cid="&lt;{relative_vcard_path}&gt;" '
                            f'cl="{relative_vcard_path}" ctt_s="null" ctt_t="null" text="null" '
                            f'data="{byte_string[2:-1]}" />\n'
                        )

                # If you don't want to convert vcards with locations to plain text MMS, uncomment this section.
                #with vcard_path.open("rb") as fb:
                    #vcard_bytes = fb.read()
                    #byte_string = f"{b64encode(vcard_bytes)}"
                    # Use the full path and then derive the relative path, ensuring the complete filename is used
                    #relative_vcard_path = vcard_path.relative_to(Path.cwd())
                    #vcard_parts += (
                    #f'    <part seq="0" ct="text/x-vCard" name="{relative_vcard_path}" '
                    #f'chset="null" cd="null" fn="null" cid="&lt;{relative_vcard_path}&gt;" '
                    #f'cl="{relative_vcard_path}" ctt_s="null" ctt_t="null" text="null" '
                    #f'data="{byte_string[2:-1]}" />\n'
                #)
        else:
            text_only=1
        if extracted_url:
            message_text = "Dropped pin&#10;" + extracted_url
        else:
            message_text = get_message_text(message)
        #message_text = get_message_text(message)
        time = get_time_unix(message)
        participants_xml = ""
        msg_box = 2 if sent_by_me else 1
        m_type = 128 if sent_by_me else 132
        for participant in participants:
            participant_is_sender = participant == sender or (
                sent_by_me and participant == "Me"
            )
            participant_values = {
                "number": participant,
                "code": 137 if participant_is_sender else 151,
            }
            participants_xml += (
                '    <addr address="%(number)s" charset="106" type="%(code)s"/> \n'
                % participant_values
            )

            mms_text = (
                f'<mms address="{participants_text}" ct_t="application/vnd.wap.multipart.related" '
                f'date="{time}" m_type="{m_type}" msg_box="{msg_box}" read="1" '
                f'rr="129" seen="1" sim_slot="1" sub_id="-1" text_only="{text_only}"> \n'
                "  <parts> \n"
            )

            # This skips the plain text part in an MMS message if it contains the phrases "MMS Sent" or "MMS Received".
            if message_text not in ["MMS Sent", "MMS Received"]:
                mms_text += f'    <part ct="text/plain" seq="0" text="{message_text}"/> \n'

            mms_text += image_parts
            mms_text += video_parts
            mms_text += vcard_parts

            mms_text += (
                "  </parts> \n"
                "  <addrs> \n"
                f"{participants_xml}"
                "  </addrs> \n"
                "</mms> \n"
                )

        sms_backup_file.write(mms_text)

    sms_backup_file.close()

def get_message_type(message):  # author_raw = messages_raw[i].cite
    author_raw = message.cite
    if not author_raw.span:
        return 2
    else:
        return 1

    return 0

def get_message_text(message):
    # Attempt to properly translate newlines. Might want to translate other HTML here, too.
    # This feels very hacky, but couldn't come up with something better.
    # Added additional replace() calls to strip out special character that were causing issues with importing the XML file.
    message_text = str(message.find("q")).strip()[3:-4].replace("<br/>", "&#10;").replace("'", "&apos;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

    return message_text

def get_mms_sender(message, participants):
    number_text = message.cite.a["href"][4:]
    if number_text != "":
        number = format_number(phonenumbers.parse(number_text, None))
    else:
        assert (
            len(participants) == 1
        ), "Unable to determine sender in mms with multiple participants"
        number = participants[0]
    return number


def get_first_phone_number(messages, fallback_number):
    # handle group messages
    for author_raw in messages:
        if not author_raw.span:
            continue

        sender_data = author_raw.cite
        # Skip if first number is Me
        if sender_data.text == "Me":
            continue
        phonenumber_text = sender_data.a["href"][4:]
        # Sometimes the first entry is missing a phone number
        if phonenumber_text == "":
            continue

        try:
            phone_number = phonenumbers.parse(phonenumber_text, None)
        except phonenumbers.phonenumberutil.NumberParseException:
            return phonenumber_text, sender_data

        # sender_data can be used as participant for mms
        return format_number(phone_number), sender_data

    # fallback case, use number from filename
    if fallback_number != 0 and len(fallback_number) >= 7:
        fallback_number = format_number(phonenumbers.parse(fallback_number, None))
    # Create dummy participant
    sender_data = BeautifulSoup(
        f'<cite class="sender vcard"><a class="tel" href="tel:{fallback_number}"><abbr class="fn" '
        'title="">Foo</abbr></a></cite>',
        features="html.parser",
    )
    return fallback_number, sender_data


def get_participant_phone_numbers(participants_raw):
    participants = []

    for participant_set in participants_raw:
        for participant in participant_set:
            if not hasattr(participant, "a"):
                continue

            phone_number_text = participant.a["href"][4:]
            assert (
                phone_number_text != "" and phone_number_text != "0"
            ), "Could not find participant phone number. Usually caused by empty tel field."
            try:
                participants.append(
                    format_number(phonenumbers.parse(phone_number_text, None))
                )
            except phonenumbers.phonenumberutil.NumberParseException:
                participants.append(phone_number_text)

    return participants


def format_number(phone_number):
    return phonenumbers.format_number(phone_number, phonenumbers.PhoneNumberFormat.E164)


def get_time_unix(message):
    time_raw = message.find(class_="dt")
    ymdhms = time_raw["title"]
    time_obj = dateutil.parser.isoparse(ymdhms)
    # Changed this line to get the full date value including milliseconds.
    mstime = time.mktime(time_obj.timetuple()) * 1000 + time_obj.microsecond // 1000
    return int(mstime)


def write_header(filename, numsms):
    # Prepend header in memory efficient manner since the output file can be huge
    with NamedTemporaryFile(dir=Path.cwd(), delete=False) as backup_temp:
        backup_temp.write(b"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n")
        backup_temp.write(b"<!--Converted from GV Takeout data -->\n")
        backup_temp.write(bytes(f'<smses count="{str(numsms)}">\n', encoding="utf8"))
        with open(filename, "rb") as backup_file:
            copyfileobj(backup_file, backup_temp)
    # Overwrite output file with temp file
    move(backup_temp.name, filename)

main()
