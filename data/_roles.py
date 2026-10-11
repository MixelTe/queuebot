from bafser import RolesBase

# from data import Operations


class Roles(RolesBase):
    user = 2


Roles.ROLES = {
    Roles.user: {
        "name": "User",
        "operations": []
    },
}
