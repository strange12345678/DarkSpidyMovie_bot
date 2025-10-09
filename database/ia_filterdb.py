from struct import pack
import re
import base64
from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError
from info import FILES_DATABASE, FILES_DATABASE_2, FILES_DATABASE_3, DATABASE_NAME, COLLECTION_NAME, MAX_BTN

clients = []
databases = []
instances = []
media_models = []

for db_uri in [FILES_DATABASE, FILES_DATABASE_2, FILES_DATABASE_3]:
    if db_uri:
        client = AsyncIOMotorClient(db_uri)
        db = client[DATABASE_NAME]
        clients.append(client)
        databases.append(db)
        inst = Instance.from_db(db)
        instances.append(inst)
        
        @inst.register
        class Media(Document):
            file_id = fields.StrField(attribute="_id")
            file_ref = fields.StrField(allow_none=True)
            file_name = fields.StrField(required=True)
            file_size = fields.IntField(required=True)
            mime_type = fields.StrField(allow_none=True)
            caption = fields.StrField(allow_none=True)
            file_type = fields.StrField(allow_none=True)

            class Meta:
                indexes = ("$file_name",)
                collection_name = COLLECTION_NAME
        
        media_models.append(Media)

if not instances:
    raise ValueError("At least one FILES_DATABASE must be configured")

mydb = databases[0]
instance = instances[0]
Media = media_models[0]

MAX_DB_SIZE = 500 * 1024 * 1024

async def get_files_db_size():
    return (await mydb.command("dbstats"))["dataSize"]


async def get_files_db_size_2():
    if len(databases) > 1:
        return (await databases[1].command("dbstats"))["dataSize"]
    return 0


async def get_files_db_size_3():
    if len(databases) > 2:
        return (await databases[2].command("dbstats"))["dataSize"]
    return 0


async def get_target_db_index():
    db1_size = await get_files_db_size()
    
    if db1_size < MAX_DB_SIZE:
        return 0
    
    if len(databases) > 1:
        db2_size = await get_files_db_size_2()
        if db2_size < MAX_DB_SIZE:
            return 1
    
    if len(databases) > 2:
        db3_size = await get_files_db_size_3()
        if db3_size < MAX_DB_SIZE:
            return 2
    
    return 0


async def save_file(media):
    """Save file in database"""

    file_id, file_ref = unpack_new_file_id(media.file_id)
    file_name = re.sub(r"(_|\-|\.|\+)", " ", str(media.file_name))
    
    db_index = await get_target_db_index()
    MediaModel = media_models[db_index]
    
    try:
        file = MediaModel(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            mime_type=media.mime_type,
            caption=media.caption.html if media.caption else None,
            file_type=media.mime_type.split("/")[0],
        )
    except ValidationError:
        print("Error occurred while saving file in database")
        return "err"
    else:
        try:
            await file.commit()
        except DuplicateKeyError:
            print(
                f'{getattr(media, "file_name", "NO_FILE")} is already saved in database'
            )
            return "dup"
        else:
            print(f'{getattr(media, "file_name", "NO_FILE")} is saved to database {db_index + 1}')
            return "suc"


async def get_search_results(query, max_results=MAX_BTN, offset=0, lang=None):
    query = query.strip()
    if not query:
        raw_pattern = "."
    elif " " not in query:
        raw_pattern = r"(\b|[\.\+\-_])" + query + r"(\b|[\.\+\-_])"
    else:
        raw_pattern = query.replace(" ", r".*[\s\.\+\-_]")
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except:
        regex = query
    filter = {"file_name": regex}
    
    all_files = []
    
    for MediaModel in media_models:
        cursor = MediaModel.find(filter)
        cursor.sort("$natural", -1)
        files = await cursor.to_list(length=None)
        all_files.extend(files)
    
    if lang:
        lang_files = [file for file in all_files if lang in file.file_name.lower()]
        total_results = len(lang_files)
        files = lang_files[offset:][:max_results]
        next_offset = offset + max_results
        if next_offset >= total_results:
            next_offset = ""
        return files, next_offset, total_results
    
    total_results = len(all_files)
    files = all_files[offset:][:max_results]
    next_offset = offset + max_results
    if next_offset >= total_results:
        next_offset = ""
    return files, next_offset, total_results


async def get_bad_files(query, file_type=None, offset=0, filter=False):
    query = query.strip()
    if not query:
        raw_pattern = "."
    elif " " not in query:
        raw_pattern = r"(\b|[\.\+\-_])" + query + r"(\b|[\.\+\-_])"
    else:
        raw_pattern = query.replace(" ", r".*[\s\.\+\-_]")
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except:
        return []
    filter = {"file_name": regex}
    if file_type:
        filter["file_type"] = file_type
    
    all_files = []
    
    for MediaModel in media_models:
        cursor = MediaModel.find(filter)
        cursor.sort("$natural", -1)
        files = await cursor.to_list(length=None)
        all_files.extend(files)
    
    total_results = len(all_files)
    return all_files, total_results


async def get_file_details(query):
    filter = {"file_id": query}
    
    for MediaModel in media_models:
        cursor = MediaModel.find(filter)
        filedetails = await cursor.to_list(length=1)
        if filedetails:
            return filedetails
    
    return []


def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")


def encode_file_ref(file_ref: bytes) -> str:
    return base64.urlsafe_b64encode(file_ref).decode().rstrip("=")


def unpack_new_file_id(new_file_id):
    """Return file_id, file_ref"""
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash,
        )
    )
    file_ref = encode_file_ref(decoded.file_reference)
    return file_id, file_ref
