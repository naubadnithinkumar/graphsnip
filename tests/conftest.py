import textwrap

import pytest

from graphsnip.project import Project

FILES = {
    "app/validators.py": '''
        import re

        EMAIL = re.compile(r"[^@]+@[^@]+")


        def validate_name(name):
            """Reject empty or very long names.

            This docstring is deliberately long so that the shortener has
            something to collapse. It explains the rules in far more detail
            than any caller needs when reading the function body.

            Rules:
                - must not be empty
                - at most 64 characters
                - no control characters
            """
            if not name or len(name) > 64:
                raise ValueError("bad name")
            return name.strip()


        def validate_email(email):
            if not EMAIL.match(email):
                raise ValueError("bad email")
            return email.lower()
    ''',
    "app/repo.py": '''
        class Repository:
            def __init__(self):
                self.rows = {}

            def save(self, key, value):
                self.rows[key] = value
                return key

            def load(self, key):
                return self.rows.get(key)

            def remove(self, key):
                return self.rows.pop(key, None)
    ''',
    "app/services.py": '''
        from app.repo import Repository
        from app.validators import validate_name, validate_email


        class UserService:
            def __init__(self, repo: Repository):
                self.repo = repo

            def create_user(self, name, email):
                name = validate_name(name)
                email = validate_email(email)
                return self.repo.save(name, {"email": email})

            def delete_user(self, name):
                return self.repo.remove(validate_name(name))

            def get_user(self, name):
                return self.repo.load(name)


        def make_service():
            return UserService(Repository())
    ''',
    "app/api.py": '''
        from app.services import make_service

        service = make_service()


        def handle_signup(payload):
            """Entry point for POST /signup."""
            return service.create_user(payload["name"], payload["email"])


        def handle_delete(payload):
            return service.delete_user(payload["name"])
    ''',
    "web/util.js": '''
        function formatName(name) {
          if (!name) {
            return "";
          }
          return name.trim().toLowerCase();
        }

        const greet = (name) => {
          return "hello " + formatName(name);
        };

        module.exports = { formatName, greet };
    ''',
}


@pytest.fixture(scope="session")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("tinyrepo")
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    Project(root).build()
    return root
