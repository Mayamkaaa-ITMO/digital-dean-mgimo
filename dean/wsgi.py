from .db import database_path
from .web import create_app

application = create_app(database_path())
