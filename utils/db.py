from beanie import Document as BeanieDocument


class Document(BeanieDocument):
    def __hash__(self):
        return hash(self.id)
