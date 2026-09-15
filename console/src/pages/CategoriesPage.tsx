import { Flex, Heading, Table, Text } from "@radix-ui/themes";
import { ProtoNotice } from "../components/ProtoNotice";
import { CATEGORIES, itemsByCategory } from "../mock/catalog";

export function CategoriesPage() {
  return (
    <Flex direction="column" gap="3">
      <ProtoNotice />
      <Heading size="5">Catégories</Heading>
      <Text size="2" color="gray">
        Noms éditoriaux et requêtes Beets — catalogue mock, sans fichiers.
      </Text>
      <Table.Root variant="surface">
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeaderCell>Nom</Table.ColumnHeaderCell>
            <Table.ColumnHeaderCell>Requête Beets</Table.ColumnHeaderCell>
            <Table.ColumnHeaderCell>Titres mock</Table.ColumnHeaderCell>
            <Table.ColumnHeaderCell>Si vide</Table.ColumnHeaderCell>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {CATEGORIES.map((cat) => (
            <Table.Row key={cat.name}>
              <Table.RowHeaderCell>{cat.name}</Table.RowHeaderCell>
              <Table.Cell>
                <code>{cat.query}</code>
              </Table.Cell>
              <Table.Cell>{itemsByCategory(cat.name).length}</Table.Cell>
              <Table.Cell>
                <code>{cat.fallback}</code>
              </Table.Cell>
            </Table.Row>
          ))}
        </Table.Body>
      </Table.Root>
    </Flex>
  );
}
