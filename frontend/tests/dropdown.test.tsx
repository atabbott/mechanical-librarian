import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { Dropdown } from '../src/components/Dropdown'

const options = ['Apple', 'Banana', 'Blueberry', 'Blue Java', 'Cherry'].map((label) => ({
  value: label.toLowerCase(),
  label,
}))
const Fruit = () => {
  const [value, setValue] = useState('apple')
  return <Dropdown ariaLabel="Fruit" value={value} options={options} onChange={setValue} />
}

describe('Dropdown type-to-select (#1466)', () => {
  it('moves to the first option starting with what was typed, and Enter chooses it', async () => {
    const user = userEvent.setup()
    render(<Fruit />)
    await user.click(screen.getByRole('combobox', { name: 'Fruit' }))

    await user.keyboard('b')
    expect(screen.getByRole('option', { name: 'Banana' })).toHaveFocus()
    await user.keyboard('lu')
    expect(screen.getByRole('option', { name: 'Blueberry' })).toHaveFocus()
    await user.keyboard('e ')
    expect(screen.getByRole('option', { name: 'Blue Java' })).toHaveFocus()
    await user.keyboard('x')
    expect(screen.getByRole('option', { name: 'Blue Java' })).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(screen.getByRole('combobox', { name: 'Fruit' })).toHaveTextContent('Blue Java')
    expect(screen.getByRole('combobox', { name: 'Fruit' })).toHaveFocus()
    // Reopening at once starts a new prefix rather than continuing the last one.
    await user.keyboard('{Enter}c')
    expect(screen.getByRole('option', { name: 'Cherry' })).toHaveFocus()
  })

  it('starts a new prefix after a pause and keeps Space as choose when nothing was typed', async () => {
    const user = userEvent.setup()
    render(<Fruit />)
    await user.click(screen.getByRole('combobox', { name: 'Fruit' }))

    await user.keyboard('b')
    await new Promise((resolve) => setTimeout(resolve, 550))
    await user.keyboard('c')
    expect(screen.getByRole('option', { name: 'Cherry' })).toHaveFocus()
    await new Promise((resolve) => setTimeout(resolve, 550))
    await user.keyboard(' ')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(screen.getByRole('combobox', { name: 'Fruit' })).toHaveTextContent('Cherry')
  })
})
